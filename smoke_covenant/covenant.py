"""Assemble, sign, and independently verify a Render Covenant.

WHAT A COVENANT ASSERTS, stated as narrowly as it is true:

    This organization ran policy P over the ingredients its render actually
    read through a hermetic gate, got result R, and bound R to exactly these
    master bytes at a time it can prove it did not choose afterwards.

It does NOT assert that the ingredients were lawfully obtained, that a grant
signer told the truth or held authority, that a generator's training set was
clean, or that a court will read a licence the same way. Those live in
`grants.py`'s non-goals and must travel with any external description of this
format.

The verifier below deliberately imports only `cryptography` and the two shared
primitives, so it can be lifted into the public `smoke-verify` repo without
dragging the writer with it.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from typing import Mapping, Sequence

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.ec import ECDSA
from cryptography.hazmat.primitives.asymmetric.utils import Prehashed, encode_dss_signature
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from ._vendor.primitives import (
    canonical_json_bytes,
    merkle_proof_v0,
    merkle_root_v0,
    verify_inclusion_v0,
)

from .gate import HermeticGate, Ingredient
from .grants import CovenantError, digest_file

COVENANT_SIGN_DOMAIN = b"SMOKE-COVENANT-V0\x00"
COVENANT_ANCHOR_DOMAIN = b"SMOKE-COVENANT-ANCHOR-V0\x00"
COVENANT_VERSION = "smoke.covenant.v0"


def covenant_id(signing_digest: bytes, r_hex: str, s_hex: str) -> bytes:
    """The 32 bytes a trusted timestamp is taken over.

    WHY NOT THE BODY, AND WHY NOT THE SIGNING DIGEST ALONE.
    The anchor cannot live inside the signed body -- the body would then have to
    contain a token taken over itself. So the anchor sits beside the signature,
    and it must bind the SIGNED covenant rather than merely the bytes that were
    about to be signed: anyone can compute a signing digest without holding the
    key, so a timestamp over that alone proves only that someone drafted a body.
    Folding r and s in means the token witnesses a covenant that was actually
    signed, at a time the signer did not choose.
    """
    return hashlib.sha256(
        COVENANT_ANCHOR_DOMAIN + signing_digest + bytes.fromhex(r_hex) + bytes.fromhex(s_hex)
    ).digest()


class CovenantInvalid(CovenantError):
    """Verification failed. Fail closed — never downgrade to a warning."""


def _signing_digest(body: Mapping[str, object]) -> bytes:
    return hashlib.sha256(COVENANT_SIGN_DOMAIN + canonical_json_bytes(body)).digest()


@dataclass(frozen=True)
class Covenant:
    body: dict
    signature_r: str
    signature_s: str
    signer_spki: str
    anchor: dict | None = None  # beside the signature, never inside the body

    def to_dict(self) -> dict:
        return {
            "body": self.body,
            "signature": {
                "alg": "ecdsa-p256-sha256",
                "r": self.signature_r,
                "s": self.signature_s,
                "signer_spki": self.signer_spki,
            },
            "anchor": self.anchor,
        }

    def anchor_id(self) -> bytes:
        return covenant_id(_signing_digest(self.body), self.signature_r, self.signature_s)

    @property
    def master_digest(self) -> str:
        return str(self.body["master"]["digest"])

    @property
    def ingredient_root(self) -> str:
        return str(self.body["ingredients"]["merkle_root"])


def issue(
    gate: HermeticGate,
    master_path: str,
    *,
    signer,
    renderer_identity: Mapping[str, object],
    tsa_clients: Sequence[object] = (),
) -> tuple[Covenant, list[Ingredient]]:
    """Bind the gate's recorded lineage to the exact master bytes and sign it.

    Refuses to issue when the gate recorded a refusal: a covenant over a render
    that was denied an ingredient would assert a closure that did not happen.
    """
    if gate.refusals:
        first = gate.refusals[0]
        raise CovenantError(
            f"refusing to issue: the gate denied {len(gate.refusals)} asset(s); "
            f"first was {first.path} ({first.reason}: {first.error})"
        )

    ingredients = gate.ingredients
    if not ingredients:
        raise CovenantError(
            "refusing to issue a covenant over zero ingredients — it would prove nothing"
        )

    root = merkle_root_v0([i.leaf() for i in ingredients])

    body = {
        "version": COVENANT_VERSION,
        "master": {
            "digest": digest_file(master_path),
            "hash_alg": "sha256",
        },
        "ingredients": {
            "count": len(ingredients),
            "merkle_root": root.hex(),
            "leaf_domain": "SMOKE-COVENANT-INGREDIENT-V0",
        },
        "decision": {
            "policy_id": gate.policy.policy_id,
            "policy_hash": gate.policy.policy_hash(),
            "result": "ALLOW",
            "hermetic": gate.hermetic,
        },
        "context": gate.context,
        "renderer": dict(renderer_identity),
    }

    digest = _signing_digest(body)
    r, s = signer.sign(digest)
    spki = hashlib.sha256(
        signer.public_key().public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    ).hexdigest()

    witnesses = []
    if tsa_clients:
        anchored = covenant_id(digest, r.hex(), s.hex()).hex()
        for client in tsa_clients:
            witnesses.append(client.request_witness(anchored))
    anchor_block = (
        {"anchored_hash": covenant_id(digest, r.hex(), s.hex()).hex(),
         "witnesses": witnesses}
        if witnesses else None
    )

    return Covenant(body=body, signature_r=r.hex(), signature_s=s.hex(),
                    signer_spki=spki, anchor=anchor_block), ingredients


# --- verification: what a distributor or insurer runs, offline ---------------


def verify(
    covenant: Covenant,
    master_path: str,
    trusted_pubkey: ec.EllipticCurvePublicKey,
    *,
    require_anchor: bool = True,
    pinned_tsa_spki_ders: Sequence[bytes] = (),
) -> dict:
    """Verify a covenant against the delivered file. Raises CovenantInvalid.

    Requires NO network and NO contact with the issuer. That is the property —
    a distributor who must call the studio's server to check a claim has not
    verified anything, they have asked the claimant.

    `require_anchor` defaults to True and that default is the point. Without a
    trusted timestamp the covenant carries only the signer's own word about when
    it was issued, so a studio in a dispute could sign one today and assert it
    predates release. An unanchored covenant is therefore refused rather than
    quietly accepted.

    Returns the anchor report so a caller can show WHICH witnesses verified and
    whether their TSA signatures were checked. With no pinned TSA SPKIs the
    token's own signature cannot be checked, and such witnesses are reported as
    UNVERIFIED — structurally sound and imprint-bound, but not proof of time.
    """
    der = trusted_pubkey.public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
    if hashlib.sha256(der).hexdigest() != covenant.signer_spki:
        raise CovenantInvalid("signer_spki does not match the trusted key — wrong signer")

    sig = encode_dss_signature(int(covenant.signature_r, 16), int(covenant.signature_s, 16))
    try:
        trusted_pubkey.verify(sig, _signing_digest(covenant.body), ECDSA(Prehashed(SHA256())))
    except InvalidSignature as exc:  # pragma: no cover - exercised by the tamper test
        raise CovenantInvalid("signature does not verify over the canonical body") from exc

    actual = digest_file(master_path)
    if actual != covenant.master_digest:
        raise CovenantInvalid(
            f"master digest mismatch: covenant binds {covenant.master_digest[:16]}... "
            f"but the delivered file hashes to {actual[:16]}... -- this covenant does not "
            "cover these bytes"
        )

    if covenant.body.get("decision", {}).get("result") != "ALLOW":
        raise CovenantInvalid("covenant does not carry an ALLOW decision")

    return _verify_anchor(covenant, require_anchor, pinned_tsa_spki_ders)


def _verify_anchor(
    covenant: Covenant, require_anchor: bool, pinned: Sequence[bytes]
) -> dict:
    """Check the trusted-time witnesses over this covenant's anchor id."""
    from ._vendor.rfc3161 import message_imprint_digest, verify_timestamp_token

    block = covenant.anchor
    if not block or not block.get("witnesses"):
        if require_anchor:
            raise CovenantInvalid(
                "covenant carries no trusted-time anchor, so its issue time rests "
                "entirely on the signer's own word -- refusing (pass "
                "require_anchor=False to accept a self-timed covenant)"
            )
        return {"anchored": False, "witnesses": [], "verified": 0, "signature_checked": 0}

    expected = covenant.anchor_id()
    if block.get("anchored_hash") != expected.hex():
        raise CovenantInvalid(
            "anchor is bound to a different covenant: anchored_hash does not match "
            "SHA256(domain || signing_digest || r || s) for this body and signature"
        )

    # The TSA imprint is SHA-256 over the ASCII HEX of the anchored hash, not over
    # its raw bytes (rfc3161.py:54-59). Passing the raw digest here fails every
    # token with a messageImprint mismatch, which looks like a broken TSA.
    imprint = message_imprint_digest(expected.hex())

    reports, verified, checked = [], 0, 0
    for w in block["witnesses"]:
        if w.get("status") != "ok" or not w.get("token_b64"):
            reports.append({"url": w.get("url"), "valid": False,
                            "reason": w.get("error", "witness not granted")})
            continue
        res = verify_timestamp_token(
            base64.b64decode(w["token_b64"]), imprint, pinned,
            require_signature=bool(pinned),
        )
        reports.append({"url": w.get("url"), "valid": bool(res.get("valid")),
                        "gen_time": res.get("gen_time"),
                        "signature_checked": bool(res.get("signature_checked")),
                        "reason": res.get("reason")})
        if res.get("valid"):
            verified += 1
            if res.get("signature_checked"):
                checked += 1

    if require_anchor and verified == 0:
        raise CovenantInvalid(
            "no trusted-time witness verified against this covenant: "
            + "; ".join(f"{r['url']}: {r.get('reason')}" for r in reports)
        )

    return {"anchored": True, "witnesses": reports,
            "verified": verified, "signature_checked": checked}


def prove_ingredient(
    ingredients: Sequence[Ingredient], index: int
) -> tuple[dict, list[str]]:
    """Selective disclosure: reveal ONE ingredient plus its inclusion path.

    This is why the tree is worth having. A studio challenged about one
    photograph opens that grant and nothing else — the rest of the rights
    package stays sealed while the root still proves the set was fixed.
    """
    leaves = [i.leaf() for i in ingredients]
    path = merkle_proof_v0(leaves, index)
    return ingredients[index].record(), [h.hex() for h in path]


def verify_ingredient(
    record: Mapping[str, object],
    path_hex: Sequence[str],
    index: int,
    count: int,
    root_hex: str,
) -> None:
    """Check one disclosed ingredient against a covenant's root."""
    leaf = hashlib.sha256(
        b"SMOKE-COVENANT-INGREDIENT-V0\x00" + canonical_json_bytes(dict(record))
    ).digest()
    ok = verify_inclusion_v0(
        bytes.fromhex(root_hex), leaf, index, count, [bytes.fromhex(h) for h in path_hex]
    )
    if not ok:
        raise CovenantInvalid(
            "ingredient is not under the covenanted root — it was not part of this render"
        )
