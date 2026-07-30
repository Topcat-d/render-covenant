"""Prove this repo runs WITHOUT smoke-suite. The point of the vendoring pass.

Until the primitives were vendored, cloning this repo got you a package that
imported `smoke_trust` and therefore could not run at all. This asserts the
dependency is genuinely gone -- not by reading imports, but by refusing to
proceed if `smoke_trust` is importable and then exercising the full path anyway:
gate -> issue -> verify -> selective disclosure -> tamper -> C2PA round trip.

Only third-party dependency: `cryptography`.

  python test_standalone.py
"""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent))

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"\n         {detail}" if detail else ""))
    if not ok:
        failures.append(name)


def main() -> int:
    print("=" * 74)
    print("Standalone check — no smoke-suite source on the path")
    print("=" * 74)

    # 1. The dependency must be genuinely absent, not merely unused.
    try:
        import smoke_trust  # noqa: F401
        check("smoke_trust is NOT importable", False,
              f"found at {smoke_trust.__file__} — this run does not prove standalone")
    except ImportError:
        check("smoke_trust is NOT importable", True,
              "so anything that follows genuinely runs without it")

    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.asymmetric.utils import (
        Prehashed, decode_dss_signature)
    from cryptography.hazmat.primitives.hashes import SHA256

    from smoke_covenant import (
        AssetStore, CovenantInvalid, Grant, HermeticGate, issue,
        prove_ingredient, verify, verify_ingredient)
    from smoke_covenant.c2pa import covenant_from_manifest, covenant_manifest
    from smoke_covenant.policies import LICENCE_TERMS, media_licence_policy

    class Signer:
        def __init__(self):
            self._k = ec.generate_private_key(ec.SECP256R1())

        def sign(self, digest: bytes):
            r, s = decode_dss_signature(
                self._k.sign(digest, ec.ECDSA(Prehashed(SHA256()))))
            return r.to_bytes(32, "big"), s.to_bytes(32, "big")

        def public_key(self):
            return self._k.public_key()

    tmp = Path(tempfile.mkdtemp(prefix="covenant-standalone-"))
    asset, master = tmp / "surfer.jpg", tmp / "final-ad.mp4"
    asset.write_bytes(b"<licensed photo bytes>")
    master.write_bytes(b"MP4:<licensed photo bytes>")

    store = AssetStore()
    store.register(asset, Grant(
        grant_id="licence:CreativeML OpenRAIL-M", asset_digest="",
        kind="model_licence", terms=LICENCE_TERMS["creativeml-openrail-m"],
        signer_spki="huggingface:author"), label="surfer.jpg")

    ctx = {"production": "Campaign-482", "territory": "US",
           "channels": ["paid-social"], "release_end": "2027-02-01",
           "commercial": True, "intended_uses": ["advertising"]}
    gate = HermeticGate(store, media_licence_policy(), ctx)
    gate.admit(asset, "photograph")

    signer = Signer()
    cov, ingredients = issue(gate, str(master), signer=signer,
                             renderer_identity={"engine": "standalone-check"})
    check("gate admitted and a covenant issued", len(ingredients) == 1)

    rep = verify(cov, str(master), signer.public_key(), require_anchor=False)
    check("covenant verifies offline", rep["anchored"] is False)

    record, path = prove_ingredient(ingredients, 0)
    verify_ingredient(record, path, 0, len(ingredients), cov.ingredient_root)
    check("selective disclosure proves one ingredient", True,
          f"{record['grant_id']} via {len(path)} sibling hashes")

    b = bytearray(master.read_bytes())
    b[-1] ^= 0x01
    (tmp / "tampered.mp4").write_bytes(bytes(b))
    try:
        verify(cov, str(tmp / "tampered.mp4"), signer.public_key(), require_anchor=False)
        check("one flipped byte is refused", False, "a tampered master verified")
    except CovenantInvalid:
        check("one flipped byte is refused", True)

    manifest = covenant_manifest(cov, ingredients=ingredients)
    back = covenant_from_manifest(manifest)
    check("C2PA round trip preserves the covenant",
          back.to_dict() == cov.to_dict())

    # The Merkle path is the vendored code — prove it is actually exercised here.
    from smoke_covenant._vendor.primitives import merkle_root_v0
    check("vendored merkle reproduces the covenanted root",
          merkle_root_v0([i.leaf() for i in ingredients]).hex() == cov.ingredient_root)

    print("=" * 74)
    if failures:
        print(f"FAILED: {len(failures)} -> {', '.join(failures)}")
        return 1
    print("Runs standalone. Only third-party dependency is `cryptography`.")
    return 0


def test_main():
    assert main() == 0


if __name__ == "__main__":
    raise SystemExit(main())
