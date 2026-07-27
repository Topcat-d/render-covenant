"""Emit a Render Covenant as a C2PA custom assertion — ride the rail, do not race it.

WHY THIS EXISTS, stated as the strategic bet it is:

    C2PA is a RECORD FORMAT. It describes what an asset claims about itself, and
    it does that well: a manifest binds bytes with a hard binding, names a claim
    generator, enumerates ingredients, and is signed under an identity.

    A Render Covenant is a GATE DECISION. It says a policy was EVALUATED at the
    moment of use, over the ingredients a render ACTUALLY READ, with refusal as
    the live alternative outcome, and binds that decision to exact master bytes
    at a time the signer did not choose.

    C2PA has no equivalent of the second thing. There is no assertion whose
    meaning is "an enforcement boundary said yes, and would have said no."

So we do not compete with C2PA for the manifest slot — we RIDE it. The covenant
travels as a custom assertion inside somebody else's manifest, and every C2PA
aware tool in Adobe's ecosystem carries it as freight whether or not it can read
it. Distribution is the point; the format is the vehicle.

WHAT C2PA DOES NOT GIVE YOU — read this before quoting the integration:

  - It does not make the covenant's claim TRUE. A manifest is a container. If
    the gate was bypassed the covenant is false, and wrapping a false covenant
    in a signed manifest produces a better-authenticated falsehood, not a truth.
  - It does not sign under a C2PA-trusted identity unless the operator has
    configured one. This module emits an UNSIGNED manifest definition. Nothing
    here holds a certificate, and nothing here will pretend to. The covenant's
    own P-256 signature is the only signature in the emitted bytes, and it is
    NOT a C2PA claim signature.
  - A C2PA manifest can be stripped from a file entirely. Re-encode, screenshot,
    or run it through a tool that does not preserve JUMBF and the covenant is
    gone — not invalid, GONE. Absence of a covenant proves nothing, exactly as
    absence of Content Credentials proves nothing. Keep the covenant JSON in a
    side channel too; the embedded copy is for reach, not for custody.
  - C2PA validity and covenant validity are INDEPENDENT. A manifest can validate
    while the covenant inside it fails, and vice versa. `verify_manifest` checks
    only the covenant. Nothing here validates a C2PA claim signature or a
    hard binding.

DEPENDENCIES: none added. `c2pa-python` 0.37.1 is real and ships a
`c2pa_python-0.37.1-py3-none-win_amd64.whl` (checked 2026-07-27 with
`pip install --dry-run --no-deps c2pa-python`; a `c2pa` 1.4.1 also exists), so
the "does it install on Windows" question resolves YES. We still do not import
it, and that is a deliberate choice rather than an oversight: `covenant.py`'s
verifier deliberately imports only `cryptography` plus two shared primitives so
it can be lifted into the public verifier repo, and pulling a Rust-backed native
wheel into the same package would end that. What this module emits is a plain
dict in exactly the shape `c2pa.Builder(manifest_json)` consumes, so an operator
who wants embedding does `json.dumps(covenant_manifest(...))` and hands it over.
(Note: this file is `smoke_covenant/c2pa.py`; Python 3 absolute imports mean an
`import c2pa` anywhere still resolves to the real package, not to this one.)

SPEC GROUNDING — what was checked, where, and when (all fetched 2026-07-27):

  [1] Custom assertions use reverse-DNS labels, and appear in a manifest
      definition as {"label": ..., "data": {...}, "kind": "Json"} — the literal
      example given is `com.mycompany.myproduct`.
      https://opensource.contentauthenticity.org/docs/manifest/writing/assertions-actions/
  [2] Manifest definition top-level keys `claim_generator`,
      `claim_generator_info`, `assertions` (a JSON array of {label, data}),
      `title`, `format`, `thumbnail`, `ingredients`, `alg`, `ta_url`.
      https://opensource.contentauthenticity.org/docs/c2patool/docs/manifest/
  [3] Builder takes the manifest definition as a JSON string:
      `Builder(manifest_json, ctx)`, with `claim_generator` + `assertions`.
      https://opensource.contentauthenticity.org/docs/c2pa-python/docs/usage/
  [4] Label grammar and versioning: `.v2` style suffix, absent means v1;
      repeated assertions get a `__1` / `__2` instance suffix. Ingredient
      `relationship` is one of `parentOf`, `componentOf`, `inputTo`.
      https://spec.c2pa.org/specifications/specifications/2.4/specs/C2PA_Specification.html
  [5] A read report is a manifest STORE: `active_manifest` (a URI string) plus a
      `manifests` map keyed by that URI.
      https://github.com/contentauth/c2pa-python/blob/main/docs/working-stores.md
  [6] Ingredient JSON keys in c2pa-rs: `title`, `format`, `instance_id`,
      `document_id`, `relationship`, `hash`, `thumbnail`, `provenance`,
      `manifest_data`, `validation_status`, `data`, `description`,
      `informational_uri`, `metadata`, `active_manifest`.
      https://docs.rs/c2pa/latest/c2pa/struct.Ingredient.html

Only the CUSTOM ASSERTION is fully spec-grounded and fully under our control.
Everything outside `data` is somebody else's schema, is marked UNVERIFIED where
it was not confirmed, and is emitted as a convenience the operator may discard.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping, Sequence

from .covenant import COVENANT_VERSION, Covenant, CovenantInvalid
from .covenant import verify as verify_covenant
from .gate import Ingredient

# Reverse-DNS per [1]; the `.v1` tail follows the spec's version-suffix
# convention per [4] so a later incompatible shape can be `.v2` and old readers
# will simply not match it. UNVERIFIED whether validators apply the c2pa.*
# version-defaulting rule to third-party labels — we never rely on defaulting
# and always emit the suffix explicitly.
COVENANT_ASSERTION_LABEL = "org.smokeproject.render-covenant.v1"

ASSERTION_VERSION = 1

# The non-goals from grants.py say they "must travel with any external
# description of this format". A C2PA assertion is precisely an external
# description that travels, so they ride inside the payload rather than in a
# README nobody opens downstream.
NOT_ASSERTED = (
    "does not prove an undisclosed asset was not copied in out of band",
    "does not prove a generator's training set was clean",
    "does not prove a grant signer told the truth, or held authority to sign",
    "does not recognise a cropped, re-encoded or perceptually similar copy",
    "does not prove anything about bytes that never crossed the gate",
    "this assertion is not a C2PA claim signature and carries no C2PA identity",
)

# --- ingredient mapping: what lines up, and what was refused ------------------
#
# MAPPED, because the semantics genuinely coincide:
#   Ingredient.label        -> "title"           human name of an input asset [6]
#   (caller-supplied type)  -> "format"          IANA media type [6]
#   (fixed)                 -> "relationship"    "inputTo": the asset was an
#                                                input to producing the master,
#                                                not a composited component and
#                                                not a parent revision [4]
#   Ingredient.asset_digest -> "instance_id"     as a namespaced URN, see below
#
# NOT MAPPED, deliberately — forcing these would fabricate meaning:
#   grant_id      C2PA ingredients have no rights-grant field. The nearest
#                 thing is a CAWG-style metadata assertion, which is a
#                 different vocabulary with a different trust model.
#   grant_kind    same.
#   signer_spki   C2PA identity lives in the claim signature and its
#                 certificate chain. Putting a grant signer's key hash into an
#                 ingredient would read as "this ingredient was signed by X"
#                 in every viewer, which is a claim we are not making.
#   role          "photograph" / "checkpoint" / "lora" is our render-role
#                 vocabulary. `relationship` is a THREE-value enum [4] and
#                 overloading it would silently break validators.
#   merkle_root / selective disclosure
#                 C2PA enumerates ingredients in full. There is no ingredient
#                 SET commitment, so there is nothing to map the root onto and
#                 no way to express "these five were the inputs, here is proof
#                 of one, the other four stay sealed". This is the largest
#                 genuine gap and it is why the covenant is not redundant with
#                 an ingredient list.
#   the gate decision itself
#                 there is no C2PA field meaning "a policy was enforced here".
#                 That is the whole reason for the custom assertion.
#
# UNVERIFIED: `hash` [6] is documented as "the hash of the ingredient" without
# stating whether it is the asset content hash or a hash over the ingredient's
# manifest. We do NOT populate it. The authoritative digest lives in the custom
# assertion, where its meaning is ours to define; guessing at somebody else's
# field would produce a value that reads as spec-meaningful and is not.
#
# UNVERIFIED: the spec's `c2pa.ingredient.v3` assertion spells things
# `instanceID` / `dc:title` / `dc:format` [4], while the c2pa-rs JSON API spells
# them `instance_id` / `title` / `format` [6]. These are two layers (CBOR
# assertion vs library API). We emit the c2pa-rs API spelling because that is
# what `Builder` consumes [3]; a consumer reading raw CBOR will see the other.
#
# UNVERIFIED: the spec ties `instanceID` to an XMP `xmp:iid:` value. We do not
# have one. Emitting a fabricated `xmp:iid:` would be worse than emitting a
# clearly-ours URN that a strict validator can visibly reject, so we do the
# latter.
INGREDIENT_INSTANCE_URN_PREFIX = "urn:x-smokeproject:asset-sha256:"

DEFAULT_INGREDIENT_FORMAT = "application/octet-stream"


def _summary(covenant: Covenant) -> dict:
    """The flat, tool-readable view of the covenant.

    Exists because most C2PA viewers render an assertion's `data` as key/value
    pairs and will not walk into a nested signed body. It is a VIEW, never a
    source of truth: `covenant_from_manifest` recomputes it and refuses when it
    disagrees with the signed body, so a viewer cannot be shown one digest while
    verification reads another. Two copies of a fact is a lie waiting to happen;
    the cheap fix is to check them against each other on the way out.
    """
    anchor = covenant.anchor or {}
    decision = covenant.body.get("decision", {})
    ingredients = covenant.body.get("ingredients", {})
    return {
        "master_digest": covenant.master_digest,
        "master_hash_alg": covenant.body.get("master", {}).get("hash_alg"),
        "ingredient_merkle_root": covenant.ingredient_root,
        "ingredient_count": ingredients.get("count"),
        "policy_id": decision.get("policy_id"),
        "policy_hash": decision.get("policy_hash"),
        "decision": decision.get("result"),
        "hermetic": decision.get("hermetic"),
        "anchored": bool(anchor.get("witnesses")),
        "anchored_hash": anchor.get("anchored_hash"),
        "witness_count": len(anchor.get("witnesses") or ()),
        "signer_spki": covenant.signer_spki,
        "signature_alg": "ecdsa-p256-sha256",
    }


def covenant_assertion(covenant: Covenant) -> dict:
    """Render a Covenant as one C2PA custom assertion.

    Shape is {"label", "data", "kind"} per [1]. Everything inside `data` is our
    vocabulary and is the only part of this module that is fully spec-grounded,
    because a reverse-DNS custom assertion is exactly the extension point C2PA
    provides for a party with a claim the standard does not model.

    `data["covenant"]` is `Covenant.to_dict()` VERBATIM and is authoritative.
    The signing digest is taken over the canonical encoding of `body`, so any
    re-ordering, re-typing or pretty-printing that survives a JSON round trip is
    fine, and any edit is not.
    """
    return {
        "label": COVENANT_ASSERTION_LABEL,
        "data": {
            "assertion_version": ASSERTION_VERSION,
            "covenant_version": COVENANT_VERSION,
            "what_this_asserts": (
                "policy {p} was enforced by a hermetic gate over the ingredients this "
                "render actually read, the decision was {r}, and it is bound to these "
                "exact master bytes"
            ).format(
                p=covenant.body.get("decision", {}).get("policy_id"),
                r=covenant.body.get("decision", {}).get("result"),
            ),
            "not_asserted": list(NOT_ASSERTED),
            "summary": _summary(covenant),
            "covenant": covenant.to_dict(),
        },
        "kind": "Json",
    }


def ingredient_entries(
    ingredients: Sequence[Ingredient],
    *,
    format_for: Callable[[Ingredient], str] | None = None,
) -> list[dict]:
    """Map covenant Ingredients onto C2PA `ingredients` entries.

    Only the four fields listed in the module's mapping table are emitted. The
    rights facts (grant id, grant kind, signer) are NOT emitted here — see the
    table for why each one was refused. This list is a discoverability
    convenience so a C2PA viewer shows something recognisable; the covenant
    assertion remains the complete and authoritative record.

    Ordering follows `HermeticGate.ingredients` (sorted by leaf bytes), so the
    entries line up index-for-index with the Merkle leaves a `prove_ingredient`
    disclosure refers to.
    """
    fmt = format_for or (lambda _i: DEFAULT_INGREDIENT_FORMAT)
    return [
        {
            "title": ing.label,
            "format": fmt(ing),
            "relationship": "inputTo",
            "instance_id": INGREDIENT_INSTANCE_URN_PREFIX + ing.asset_digest,
        }
        for ing in ingredients
    ]


def covenant_manifest(
    covenant: Covenant,
    *,
    ingredients: Sequence[Ingredient] = (),
    title: str | None = None,
    format: str | None = None,
    claim_generator: str = "smoke-covenant/0",
    claim_generator_version: str = "0",
    format_for: Callable[[Ingredient], str] | None = None,
) -> dict:
    """A C2PA manifest DEFINITION dict carrying the covenant.

    Top-level keys are those documented for a manifest definition file [2] and
    accepted by `c2pa.Builder(json.dumps(...))` [3]. `json.dumps` this and an
    operator's own C2PA tooling can embed it.

    DELIBERATELY ABSENT: `alg`, `private_key`, `sign_cert`, `ta_url`. Those
    configure a C2PA IDENTITY, and this module has none. Emitting placeholders
    would invite someone to ship a manifest signed by a test certificate and
    believe it meant something. The operator supplies signing config or the
    manifest stays unsigned — which is the honest default, since an unsigned
    manifest is visibly unsigned and a badly-signed one is not.

    The `assertions` list contains exactly one entry. A caller merging this into
    a larger manifest should append, not replace; a manifest may legitimately
    carry the covenant alongside `c2pa.actions`, CAWG metadata and the rest.
    """
    manifest: dict[str, Any] = {
        "claim_generator": claim_generator,
        "claim_generator_info": [
            {"name": claim_generator, "version": claim_generator_version}
        ],
        "assertions": [covenant_assertion(covenant)],
    }
    if title is not None:
        manifest["title"] = title
    if format is not None:
        manifest["format"] = format
    if ingredients:
        manifest["ingredients"] = ingredient_entries(ingredients, format_for=format_for)
    return manifest


# --- getting it back out -----------------------------------------------------


def _assertion_list(manifest: Mapping[str, Any]) -> Sequence[Any]:
    """Find the assertions in either shape a caller might hand us.

    Two real shapes exist and both turn up in practice:
      * a manifest DEFINITION — `assertions` at top level [2];
      * a manifest STORE read report — `active_manifest` URI plus a `manifests`
        map keyed by it [5].
    Additionally the spec's JSON-LD serialization keys assertions BY LABEL
    rather than listing them [4], so a Mapping is accepted and normalised.
    """
    node: Mapping[str, Any] = manifest
    if "assertions" not in node and "manifests" in node:
        manifests = node.get("manifests") or {}
        if not isinstance(manifests, Mapping) or not manifests:
            raise CovenantInvalid("manifest store carries no manifests")
        uri = node.get("active_manifest")
        if uri is None:
            raise CovenantInvalid(
                "manifest store has no active_manifest, so which manifest to trust "
                "is undefined -- refusing to guess"
            )
        active = manifests.get(uri)
        if not isinstance(active, Mapping):
            raise CovenantInvalid(f"active_manifest {uri!r} is not present in manifests")
        node = active

    assertions = node.get("assertions")
    if assertions is None:
        raise CovenantInvalid("manifest carries no assertions")
    if isinstance(assertions, Mapping):
        # label-keyed form: rebuild the {label, data} shape we scan for.
        return [{"label": k, "data": v} for k, v in assertions.items()]
    if not isinstance(assertions, Sequence) or isinstance(assertions, (str, bytes)):
        raise CovenantInvalid("manifest assertions is neither a list nor a label map")
    return assertions


def _covenant_data(manifest: Mapping[str, Any]) -> Mapping[str, Any]:
    """The single covenant assertion's payload, or fail closed."""
    found = []
    for entry in _assertion_list(manifest):
        if not isinstance(entry, Mapping):
            continue
        label = entry.get("label")
        if not isinstance(label, str):
            continue
        # Exact label, or the spec's duplicate-instance suffix `__1`, `__2` [4].
        if label == COVENANT_ASSERTION_LABEL or label.startswith(
            COVENANT_ASSERTION_LABEL + "__"
        ):
            found.append(entry)

    if not found:
        raise CovenantInvalid(
            f"no {COVENANT_ASSERTION_LABEL} assertion in this manifest -- "
            "it carries no render covenant"
        )
    if len(found) > 1:
        # Which one covers the delivered bytes would be a guess, and guessing
        # here means picking whichever copy happens to verify.
        raise CovenantInvalid(
            f"manifest carries {len(found)} covenant assertions "
            f"({', '.join(str(e.get('label')) for e in found)}) -- ambiguous, refusing"
        )

    data = found[0].get("data")
    if not isinstance(data, Mapping):
        raise CovenantInvalid("covenant assertion has no object-valued data")
    return data


def covenant_from_manifest(manifest: Mapping[str, Any]) -> Covenant:
    """Extract the Covenant from a C2PA manifest dict. Raises CovenantInvalid.

    Reconstructs the dataclass and CROSS-CHECKS the flat `summary` against the
    signed body. A viewer that renders the summary and a verifier that reads the
    body must never be able to see different facts, so a disagreement is a
    refusal rather than a preference for the signed copy.

    This does not verify anything cryptographic — it hands a Covenant to the
    existing `verify()`, which is the only thing that decides validity.
    """
    data = _covenant_data(manifest)

    version = data.get("covenant_version")
    if version != COVENANT_VERSION:
        raise CovenantInvalid(
            f"covenant assertion declares {version!r}, this verifier speaks "
            f"{COVENANT_VERSION!r} -- refusing rather than reading it as if it matched"
        )

    payload = data.get("covenant")
    if not isinstance(payload, Mapping):
        raise CovenantInvalid("covenant assertion has no embedded covenant object")

    body = payload.get("body")
    signature = payload.get("signature")
    if not isinstance(body, Mapping) or not isinstance(signature, Mapping):
        raise CovenantInvalid("embedded covenant is missing its body or signature")
    for field in ("r", "s", "signer_spki"):
        if not isinstance(signature.get(field), str):
            raise CovenantInvalid(f"embedded covenant signature is missing {field!r}")

    anchor = payload.get("anchor")
    if anchor is not None and not isinstance(anchor, Mapping):
        raise CovenantInvalid("embedded covenant anchor is present but not an object")

    covenant = Covenant(
        body=dict(body),
        signature_r=str(signature["r"]),
        signature_s=str(signature["s"]),
        signer_spki=str(signature["signer_spki"]),
        anchor=dict(anchor) if anchor is not None else None,
    )

    declared = data.get("summary")
    if declared is not None:
        expected = _summary(covenant)
        if dict(declared) != expected:
            differing = sorted(
                k for k in set(expected) | set(dict(declared))
                if dict(declared).get(k) != expected.get(k)
            )
            raise CovenantInvalid(
                "the assertion's summary disagrees with the signed covenant body on "
                f"{differing} -- a viewer would show one thing and the verifier check "
                "another, so this is refused rather than resolved"
            )
    return covenant


def verify_manifest(
    manifest: Mapping[str, Any],
    master_path: str,
    trusted_pubkey,
    *,
    require_anchor: bool = True,
    pinned_tsa_spki_ders: Sequence[bytes] = (),
) -> tuple[Covenant, dict]:
    """Extract a covenant from a C2PA manifest and verify it against the file.

    Returns (covenant, anchor report). Raises CovenantInvalid on any failure,
    including extraction failures — a manifest that carries no covenant is not
    "unverified", it is refused, same as everywhere else in this package.

    SCOPE, stated because the wrapping invites the wrong assumption: this checks
    THE COVENANT ONLY. It does not validate the C2PA claim signature, the hard
    binding, the certificate chain, or any other assertion in the manifest. A
    green result here means the covenant holds over these bytes; it says nothing
    about whether the surrounding Content Credential is valid, and a C2PA
    validator's green says nothing about the covenant. Run both.
    """
    covenant = covenant_from_manifest(manifest)
    report = verify_covenant(
        covenant,
        master_path,
        trusted_pubkey,
        require_anchor=require_anchor,
        pinned_tsa_spki_ders=pinned_tsa_spki_ders,
    )
    return covenant, report
