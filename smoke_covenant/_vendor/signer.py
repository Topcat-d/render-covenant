"""DemoSigner — an in-memory P-256 signer for the demos and tests in this repo.

WHY THIS EXISTS. `covenant.py:issue()` accepts anything that satisfies a tiny
protocol -- `sign(digest: bytes) -> tuple[bytes, bytes]` (returns `(r, s)`, 32
bytes each, over a 32-byte digest) and `public_key() -> ec.EllipticCurvePublicKey`.
Production callers (the ComfyUI custom node with a configured signing key, a
KMS, an HSM, a signing daemon) supply their own object satisfying that same
protocol. The demos and tests in this directory need *something* that satisfies
it too, and previously borrowed smoke_trust's `SoftwareMeasurementSigner` for
that -- which meant every demo dragged in a smoke-suite checkout just to get an
ephemeral key. This is that same shape, vendored, so the demos need nothing
beyond `cryptography`.

WHAT THIS IS NOT. `DemoSigner` generates a fresh P-256 keypair in memory every
time it is constructed and holds the private key only for the life of the
process. It proves nothing about identity -- it verifies that a signature was
made by *some* key that also produced the delivered public key, and nothing
more. NAMED THE WAY IT IS so it cannot be mistaken for a production signer: do
not wire this into anything that issues a covenant a real party is meant to
rely on. A real deployment supplies its own signer -- backed by a KMS, an HSM,
or a signing daemon -- through the exact same two-method protocol; nothing in
`smoke_covenant` needs to change to accept one.

Mechanically identical to smoke_trust's `SoftwareMeasurementSigner`: P-256,
digest-only (`Prehashed(SHA256)`) ECDSA, DER signature decoded to raw `(r, s)`.
"""

from __future__ import annotations

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import (
    Prehashed,
    decode_dss_signature,
)


class DemoSigner:
    """Ephemeral in-memory P-256 signer. Demos and tests only -- see module
    docstring. Not a MeasurementSigner subclass (smoke_covenant does not
    import smoke_trust), but satisfies the identical duck-typed protocol
    `covenant.py:issue()` expects."""

    def __init__(self, private_key: ec.EllipticCurvePrivateKey | None = None):
        self._priv = private_key or ec.generate_private_key(ec.SECP256R1())

    def sign(self, digest: bytes) -> tuple[bytes, bytes]:
        if len(digest) != 32:
            raise ValueError("digest must be 32 bytes")
        der = self._priv.sign(digest, ec.ECDSA(Prehashed(hashes.SHA256())))
        r, s = decode_dss_signature(der)
        return r.to_bytes(32, "big"), s.to_bytes(32, "big")

    def public_key(self) -> ec.EllipticCurvePublicKey:
        return self._priv.public_key()
