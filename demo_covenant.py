"""End-to-end Render Covenant demo. No ComfyUI required.

Runs the whole chain against a stand-in renderer so the mechanism is visible in
one screen:

    register grants -> render through the gate -> issue -> verify offline
      -> disclose ONE ingredient -> swap in an unlicensed LoRA -> FAIL CLOSED
      -> flip one byte of the master -> covenant no longer applies

Run:  python demo_covenant.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):  # Windows consoles default to cp1252
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import bootstrap_suite  # noqa: E402
from _error_help import missing_dependency  # noqa: E402

# The stand-in renderer needs no ComfyUI, and the signer below is the
# vendored DemoSigner (P-256, ephemeral, in-memory -- see
# smoke_covenant/_vendor/signer.py), so this script needs no smoke-suite
# checkout at all. need_suite=False: a suite root is picked up OPTIONALLY if
# one happens to be present (harmless either way), never required.
bootstrap_suite(need_suite=False)

try:
    from smoke_covenant._vendor.signer import DemoSigner  # noqa: E402

    from smoke_covenant import (  # noqa: E402
        AssetNotRegistered,
        AssetStore,
        CovenantError,
        CovenantInvalid,
        Grant,
        HermeticGate,
        PolicyDenied,
        issue,
        prove_ingredient,
        toy_territory_window_policy,
        verify,
        verify_ingredient,
    )
except ModuleNotFoundError as exc:
    # smoke_covenant hard-depends on `cryptography` (P-256 signing/verification,
    # RFC 3161 anchoring). This is the one dependency a fresh checkout is
    # missing more often than any other, and a bare traceback several frames
    # deep in smoke_covenant.gate or _vendor.signer does not say so -- name it
    # and give the exact fix instead.
    if exc.name == "cryptography":
        missing_dependency(
            exc,
            what="'cryptography' is not installed on this interpreter "
                 "(smoke_covenant's only hard dependency)",
            remedy=f'"{sys.executable}" -m pip install cryptography',
        )
    raise  # anything else is unexpected -- keep the real traceback, don't hide it

OK, NO = "  [OK]", "  [BLOCKED]"


def line(title: str) -> None:
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


def make(tmp: Path, name: str, body: bytes) -> Path:
    p = tmp / name
    p.write_bytes(body)
    return p


def render(gate: HermeticGate, out: Path, assets: list[tuple[Path, str]]) -> Path:
    """A stand-in renderer. The ONLY way it can read an ingredient is the gate,
    which is the entire point — an unmediated read is outside the theorem."""
    blob = b""
    for path, role in assets:
        with gate.open_asset(path, role) as fh:
            blob += fh.read()
    out.write_bytes(b"MP4:" + blob)
    return out


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="covenant-demo-"))
    signer = DemoSigner()
    policy = toy_territory_window_policy()

    photo = make(tmp, "surfer.jpg", b"<photo bytes>")
    music = make(tmp, "track04.wav", b"<music bytes>")
    lora_ok = make(tmp, "brand_v3.safetensors", b"<licensed lora>")
    lora_bad = make(tmp, "scraped_v1.safetensors", b"<UNLICENSED lora>")

    store = AssetStore()
    store.register(photo, Grant(
        grant_id="asset_license_774", asset_digest="", kind="asset_license",
        terms={"territories": ["US"], "expires_on": "2027-02-01",
               "channels": ["paid-social", "web"]},
        signer_spki="demo-rightsholder"), label="surfer.jpg")
    store.register(music, Grant(
        grant_id="sync_license_118", asset_digest="", kind="sync_license",
        terms={"territories": ["US", "UK"], "expires_on": "2027-06-01",
               "channels": ["paid-social", "web"]},
        signer_spki="demo-publisher"), label="track04.wav")
    store.register(lora_ok, Grant(
        grant_id="model_grant_204", asset_digest="", kind="model_grant",
        terms={"territories": ["US", "UK"], "expires_on": "2028-01-01"},
        signer_spki="demo-brand"), label="brand_v3.safetensors")
    # lora_bad is deliberately NEVER registered.

    us = {"production": "Campaign-482", "territory": "US",
          "channels": ["paid-social"], "release_start": "2026-08-01",
          "release_end": "2027-02-01"}

    line("1. CLEAN RENDER — every ingredient resolves to a grant")
    gate = HermeticGate(store, policy, us)
    master = render(gate, tmp / "final-ad.mp4",
                    [(photo, "photograph"), (music, "music"), (lora_ok, "lora")])
    print(f"{OK} render completed, {len(gate.ingredients)} ingredients recorded from ACTUAL READS")
    for i in gate.ingredients:
        print(f"       {i.role:12} {i.label:24} {i.grant_id}")

    cov, ingredients = issue(
        gate, str(master), signer=signer,
        renderer_identity={"engine": "demo-renderer", "version": "0.1"},
    )
    print(f"{OK} covenant issued")
    print(f"       master root  {cov.master_digest[:32]}...")
    print(f"       ingredient   {cov.ingredient_root[:32]}...")
    print(f"       hermetic     {cov.body['decision']['hermetic']}")

    line("2. DISTRIBUTOR VERIFIES — offline, no contact with the issuer")
    # This demo takes no trusted timestamp (it runs with no network), so the
    # covenant is SELF-TIMED and verify() refuses it unless told otherwise.
    # That refusal is the default for a reason: without an external witness the
    # issue time is the signer's own word, and a signer in a dispute could
    # backdate. render_covenant_demo.py takes real RFC 3161 tokens.
    try:
        verify(cov, str(master), signer.public_key())
        print("  [!!] an unanchored covenant verified by default — BUG")
        return 1
    except CovenantInvalid as exc:
        print(f"{NO} default verify refuses it: {str(exc).split('--')[0].strip()}")
    rep = verify(cov, str(master), signer.public_key(), require_anchor=False)
    print(f"{OK} signature + master digest verify against the delivered file")
    print(f"       anchored={rep['anchored']} — trusted time NOT established here")

    line("3. SELECTIVE DISCLOSURE — reveal ONE grant, keep the rest sealed")
    idx = next(n for n, i in enumerate(ingredients) if i.role == "photograph")
    record, path = prove_ingredient(ingredients, idx)
    verify_ingredient(record, path, idx, len(ingredients), cov.ingredient_root)
    print(f"{OK} proved {record['grant_id']} was an input, revealing {len(path)} sibling hashes")
    print(f"       the other {len(ingredients) - 1} ingredients stay undisclosed")

    line("4. THE MONEY SHOT — swap in an unlicensed LoRA")
    gate2 = HermeticGate(store, policy, us)
    try:
        render(gate2, tmp / "bad-ad.mp4",
               [(photo, "photograph"), (music, "music"), (lora_bad, "lora")])
        print("  [!!] RENDER SUCCEEDED — the gate failed. This is a bug.")
        return 1
    except AssetNotRegistered as exc:
        print(f"{NO} render refused: {exc}")
        print(f"       the unlicensed asset never reached the renderer")

    line("5. WRONG TERRITORY — the photo is US-only, campaign goes UK")
    uk = {**us, "territory": "UK"}
    gate3 = HermeticGate(store, policy, uk)
    try:
        render(gate3, tmp / "uk-ad.mp4", [(photo, "photograph"), (music, "music")])
        print("  [!!] RENDER SUCCEEDED — the gate failed. This is a bug.")
        return 1
    except PolicyDenied as exc:
        print(f"{NO} render refused: {exc}")

    line("6. NO COVENANT OVER A DENIED RENDER")
    try:
        issue(gate3, str(master), signer=signer, renderer_identity={"engine": "demo-renderer"})
        print("  [!!] ISSUED over a refusal — this is a bug.")
        return 1
    except CovenantError as exc:
        print(f"{NO} {exc}")

    line("7. ONE BYTE CHANGES — the old covenant no longer applies")
    tampered = tmp / "final-ad-tampered.mp4"
    b = bytearray(master.read_bytes())
    b[-1] ^= 0x01
    tampered.write_bytes(bytes(b))
    try:
        verify(cov, str(tampered), signer.public_key(), require_anchor=False)
        print("  [!!] VERIFIED a tampered master — this is a bug.")
        return 1
    except CovenantInvalid as exc:
        print(f"{NO} {exc}")

    line("WHAT THIS DID AND DID NOT PROVE")
    print("""  PROVED: a policy ran over the ingredients the render ACTUALLY READ through a
          gate that could refuse, and the result is bound to exactly these bytes,
          verifiable by a third party with no access to the issuer.

  DID NOT PROVE: that an asset was lawfully obtained, that a grant signer told the
          truth or held authority, that a generator's training set was clean, or
          that a cropped/re-encoded copy would be recognised. The bundled policy is
          a labelled TOY. A read that bypasses the gate is outside the claim
          entirely -- it does not weaken it, it invalidates it.""")
    print(f"\n  artifacts: {tmp}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
