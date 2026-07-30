"""Trusted-time anchoring: can a covenant's issue time be backdated?

Before this, every covenant carried anchor=None and verify() never looked at it.
The signature proved WHO asserted the ingredients; nothing proved WHEN. A studio
in a dispute could sign a covenant today and claim it predated release -- which
contradicted the whole pitch line ("bound at a time it did not choose").

Offline by default. Pass --live to hit the real DigiCert and Sigstore TSAs.

  python test_anchor.py [--live]
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import bootstrap_suite  # noqa: E402

# Anchoring IS the thing under test, but both the signer and the RFC 3161
# client below are the vendored copies (smoke_covenant/_vendor/signer.py,
# smoke_covenant/_vendor/tsa.py), so this script -- including --live, which
# hits the real DigiCert and Sigstore TSAs -- needs no smoke-suite checkout.
bootstrap_suite(need_suite=False)

from smoke_covenant._vendor.signer import DemoSigner  # noqa: E402

from smoke_covenant import (  # noqa: E402
    AssetStore, CovenantInvalid, Grant, HermeticGate, issue, verify,
)
from smoke_covenant.covenant import covenant_id  # noqa: E402
from smoke_covenant.policies import LICENCE_TERMS, media_licence_policy  # noqa: E402

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"\n         {detail}" if detail else ""))
    if not ok:
        failures.append(name)


def build(tmp: Path, tsa_clients=()):
    asset = tmp / "photo.jpg"
    asset.write_bytes(b"<photo bytes>")
    master = tmp / "final.mp4"
    master.write_bytes(b"MP4:<photo bytes>")
    store = AssetStore()
    store.register(asset, Grant(
        grant_id="licence:CreativeML OpenRAIL-M", asset_digest="", kind="model_licence",
        terms=LICENCE_TERMS["creativeml-openrail-m"], signer_spki="demo"), label="photo.jpg")
    ctx = {"territory": "US", "channels": ["paid-social"], "release_end": "2027-02-01",
           "commercial": True, "intended_uses": ["advertising"]}
    gate = HermeticGate(store, media_licence_policy(), ctx)
    gate.admit(asset, "photograph")
    signer = DemoSigner()
    cov, ings = issue(gate, str(master), signer=signer,
                      renderer_identity={"engine": "test"}, tsa_clients=tsa_clients)
    return cov, ings, master, signer


def main() -> int:
    live = "--live" in sys.argv
    tmp = Path(tempfile.mkdtemp(prefix="covenant-anchor-"))

    print("=" * 74)
    print("Trusted-time anchoring")
    print("=" * 74)

    # 1. an unanchored covenant is REFUSED by default
    cov, _, master, signer = build(tmp)
    check("unanchored covenant has anchor=None", cov.anchor is None)
    refused = False
    try:
        verify(cov, str(master), signer.public_key())
    except CovenantInvalid as exc:
        refused, msg = True, str(exc).split("--")[0].strip()
    check("verify() REFUSES an unanchored covenant by default", refused,
          msg if refused else "it was accepted -- backdating stays possible")

    # 2. ...but can be accepted explicitly, and says so
    rep = verify(cov, str(master), signer.public_key(), require_anchor=False)
    check("require_anchor=False accepts and reports anchored=False",
          rep["anchored"] is False and rep["verified"] == 0)

    # 3. the anchor id binds the SIGNED covenant, not just the draft body
    from smoke_covenant.covenant import _signing_digest
    expect = covenant_id(_signing_digest(cov.body), cov.signature_r, cov.signature_s)
    other = covenant_id(_signing_digest(cov.body), cov.signature_r,
                        ("00" * 32))
    check("anchor id changes when the signature changes", expect != other,
          "so a timestamp over a draft body cannot be reused for a signed one")

    # 4. live TSAs
    if live:
        from smoke_covenant._vendor.tsa import (
            DEFAULT_COMMERCIAL_TSA_URL, DEFAULT_SIGSTORE_TSA_URL, TSAClient,
        )
        clients = [TSAClient(DEFAULT_COMMERCIAL_TSA_URL),
                   TSAClient(DEFAULT_SIGSTORE_TSA_URL)]
        cov2, _, master2, signer2 = build(tmp, tsa_clients=clients)
        got = cov2.anchor and cov2.anchor.get("witnesses") or []
        for w in got:
            print(f"         {w.get('url')}: {w.get('status')} "
                  f"{w.get('gen_time') or w.get('error', '')}")
        check("at least one live TSA granted a token",
              any(w.get("status") == "ok" for w in got))

        rep2 = verify(cov2, str(master2), signer2.public_key())
        for w in rep2["witnesses"]:
            print(f"         {w['url']}: valid={w['valid']} "
                  f"gen_time={w.get('gen_time')} sig_checked={w.get('signature_checked')}")
        check("anchored covenant verifies with >=1 witness", rep2["verified"] >= 1)
        check("witnesses report UNVERIFIED signatures with no pinned TSA keys",
              rep2["signature_checked"] == 0,
              "honest: imprint-bound but the TSA's own signature was not checked")

        # 5. tampering with the signature breaks the anchor binding
        bad = type(cov2)(body=cov2.body, signature_r=cov2.signature_r,
                         signature_s="11" * 32, signer_spki=cov2.signer_spki,
                         anchor=cov2.anchor)
        broke = False
        try:
            verify(bad, str(master2), signer2.public_key())
        except CovenantInvalid:
            broke = True
        check("anchor rejects a swapped signature", broke)
    else:
        print("  [SKIP] live TSA checks (pass --live)")

    print("=" * 74)
    if failures:
        print(f"FAILED: {len(failures)} -> {', '.join(failures)}")
        return 1
    print("Anchoring holds. Unanchored covenants fail closed.")
    return 0


def test_main():
    assert main() == 0


if __name__ == "__main__":
    raise SystemExit(main())
