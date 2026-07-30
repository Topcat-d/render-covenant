"""In-memory P-256 signers: one for real keys, one for demos.

WHY THIS EXISTS. `covenant.py:issue()` accepts anything that satisfies a tiny
protocol -- `sign(digest: bytes) -> tuple[bytes, bytes]` (returns `(r, s)`, 32
bytes each, over a 32-byte digest) and `public_key() -> ec.EllipticCurvePublicKey`.
Serious deployments supply their own object satisfying that protocol, backed by
a KMS, an HSM or a signing daemon. This module exists so that the two callers
which cannot do that -- the demos in this directory, and the ComfyUI node when
no smoke-suite checkout is present -- need nothing beyond `cryptography`.

TWO CLASSES, AND THE LINE BETWEEN THEM IS THE KEY, NOT THE MATHS. Both sign
identically: P-256, digest-only (`Prehashed(SHA256)`) ECDSA, DER signature
decoded to raw `(r, s)` -- mechanically the same as smoke_trust's
`SoftwareMeasurementSigner`. What differs is where the private key comes from,
which is the only part that decides whether a covenant is evidence:

  LocalKeySigner(key)   REQUIRES a key you supplied and protect. A covenant it
                        signs is exactly as attributable as that key is. This
                        is a legitimate production signer for an operator who
                        holds their own PEM -- it is a thin ECDSA wrapper and
                        adds no weakness of its own. Its limits are the
                        ordinary limits of a software key: it lives in this
                        process's memory while in use, so it is weaker than an
                        HSM and stronger than nothing.

  DemoSigner()          MINTS A FRESH EPHEMERAL KEY that dies with the process.
                        Nobody can pin it, so it proves only that the bytes are
                        self-consistent and NOTHING about who signed them. For
                        demos and tests. Do not wire it into anything that
                        issues a covenant a real party is meant to rely on.

The split is enforced by construction rather than by comment: `LocalKeySigner`
has no key-generating path, so it cannot hand back an unpinnable signer, and
`DemoSigner` is the only thing in this package that mints a key. That is what
lets the ComfyUI node fall back to `LocalKeySigner` for an operator-supplied
key without a demo-branded class ending up under production evidence -- see
`comfy_node/issue_node.py:_keyed_signer_class`.
"""

from __future__ import annotations

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import (
    Prehashed,
    decode_dss_signature,
)


class LocalKeySigner:
    """P-256 ECDSA over a private key the caller supplies. No key, no signer.

    Not a MeasurementSigner subclass (smoke_covenant does not import
    smoke_trust), but satisfies the identical duck-typed protocol
    `covenant.py:issue()` expects, over the identical signing scheme.
    """

    def __init__(self, private_key: ec.EllipticCurvePrivateKey):
        if private_key is None:
            # The whole point of this class. Generating one here would hand the
            # caller an unpinnable key while they believed they had supplied one.
            raise ValueError(
                "LocalKeySigner requires a private key. It deliberately has no "
                "key-generating path: a signer that quietly invents its own key "
                "produces covenants nobody can attribute. Pass a key, or use "
                "DemoSigner() if an ephemeral demo key is genuinely what you want."
            )
        self._priv = private_key

    def sign(self, digest: bytes) -> tuple[bytes, bytes]:
        if len(digest) != 32:
            raise ValueError("digest must be 32 bytes")
        der = self._priv.sign(digest, ec.ECDSA(Prehashed(hashes.SHA256())))
        r, s = decode_dss_signature(der)
        return r.to_bytes(32, "big"), s.to_bytes(32, "big")

    def public_key(self) -> ec.EllipticCurvePublicKey:
        return self._priv.public_key()


class DemoSigner(LocalKeySigner):
    """Ephemeral in-memory P-256 signer. Demos and tests ONLY.

    Generates a fresh keypair on construction and holds it only for the life of
    the process. It proves that a signature was made by *some* key that also
    produced the delivered public key, and nothing more -- there is no identity
    behind it to pin. NAMED THE WAY IT IS so it cannot be mistaken for a
    production signer. An operator with a real key wants `LocalKeySigner`; a
    real deployment wants its own KMS/HSM-backed object over the same two-method
    protocol, and nothing in `smoke_covenant` needs to change to accept one.
    """

    def __init__(self):
        super().__init__(ec.generate_private_key(ec.SECP256R1()))
