"""A REAL ComfyUI render through the hermetic gate, ending in a signed covenant.

This is the artifact worth showing someone: a genuine diffusion render whose
ingredient list was built from the loads ComfyUI actually performed, bound to the
exact PNG bytes, verifiable by a third party offline.

Needs models. Put at least one checkpoint in ComfyUI/models/checkpoints and
(optionally, but it is the interesting one) a LoRA in ComfyUI/models/loras.

  "C:/Users/topdy/ComfyUI/.venv/Scripts/python.exe" covenant/render_covenant_demo.py
  ... --deny-lora     register the LoRA with a UK-only grant to watch it BLOCK
  ... --steps 8       faster

Node signatures verified against ComfyUI @806e092 nodes.py:
  CheckpointLoaderSimple.load_checkpoint(ckpt_name) -> (MODEL, CLIP, VAE)   :627
  LoraLoader.load_lora(model, clip, lora_name, s_model, s_clip)             :719
  CLIPTextEncode.encode(clip, text)                                         :73
  EmptyLatentImage.generate(width, height, batch_size=1)                   :1247
  KSampler.sample(model, seed, steps, cfg, sampler_name, scheduler,
                  positive, negative, latent_image, denoise=1.0)           :1606
  VAEDecode.decode(vae, samples)                                           :330

KNOWN COVERAGE GAP, read from source rather than assumed: nodes.py:629 passes
`embedding_directory=folder_paths.get_folder_paths("embeddings")` -- a DIRECTORY
list, not get_full_path. Textual-inversion embeddings loaded from there do not
cross the gate. Per premise 4 that is an invalidating gap for any render that
uses them, not a rounding error. Closing it needs a hook inside comfy.sd, which
is deliberately out of scope for v0.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

COMFY = Path(os.environ.get("COMFYUI_ROOT", r"C:/Users/topdy/ComfyUI"))
HERE = Path(__file__).resolve().parent
SUITE = HERE.parents[0]
for p in (COMFY, SUITE / "trust", SUITE / "sdks" / "agent" / "python", HERE):
    sys.path.insert(0, str(p))


def banner(t: str) -> None:
    print(f"\n{'=' * 74}\n{t}\n{'=' * 74}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--steps", type=int, default=12)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--size", type=int, default=512)
    ap.add_argument("--prompt", default="a surfer at golden hour, cinematic advertising photography")
    ap.add_argument("--no-anchor", action="store_true",
                    help="skip the RFC 3161 timestamp (offline); the covenant is then "
                         "self-timed and verify() must be told to accept that")
    ap.add_argument("--lora", default=None,
                    help="substring selecting which LoRA to use (default: first found)")
    ap.add_argument("--non-commercial", action="store_true",
                    help="render as an internal/non-commercial piece; a CC-BY-NC LoRA "
                         "is then admissible where a paid ad would be BLOCKED")
    args = ap.parse_args()

    import folder_paths
    checkpoints = folder_paths.get_filename_list("checkpoints")
    loras = folder_paths.get_filename_list("loras")
    if not checkpoints:
        print("NO CHECKPOINTS FOUND in " + str(COMFY / "models" / "checkpoints"))
        print("Drop an SD1.5 or SDXL .safetensors there and re-run.")
        return 2
    ckpt_name = checkpoints[0]
    if args.lora:
        matches = [n for n in loras if args.lora.lower() in n.lower()]
        if not matches:
            print(f"no LoRA matching {args.lora!r} in: {loras}")
            return 2
        lora_name = matches[0]
    else:
        lora_name = loras[0] if loras else None
    print(f"checkpoint: {ckpt_name}\nlora:       {lora_name or '(none found)'}")

    import nodes  # noqa: E402
    from smoke_trust.capsule.measurement import SoftwareMeasurementSigner  # noqa: E402
    from smoke_covenant import (  # noqa: E402
        AssetStore, CovenantInvalid, Grant, HermeticGate,
        issue, prove_ingredient, verify, verify_ingredient,
    )
    from smoke_covenant.policies import LICENCE_TERMS, media_licence_policy  # noqa: E402
    from smoke_covenant.adapters.comfy import ComfyGateRefusal, covenant_gate  # noqa: E402

    # --- register what the studio has cleared -------------------------------
    # Real licences, transcribed by a human from the published texts. The point of
    # using real ones: they broke the toy policy's vocabulary immediately, because
    # what separates these assets is commercial_use, which it could not express.
    def licence_for(filename: str) -> dict:
        f = filename.lower()
        if "dmd2" in f:
            return LICENCE_TERMS["cc-by-nc-4.0"]           # commerce PROHIBITED
        if "sd_xl_base" in f or "sdxl_base" in f:
            return LICENCE_TERMS["creativeml-openrail++-m"]
        return LICENCE_TERMS["creativeml-openrail-m"]

    store = AssetStore()
    ck_path = folder_paths.get_full_path_or_raise("checkpoints", ckpt_name)
    store.register(ck_path, Grant(
        grant_id=f"licence:{licence_for(ckpt_name)['licence']}", asset_digest="",
        kind="model_licence", terms=licence_for(ckpt_name),
        signer_spki="huggingface:stabilityai"), label=ckpt_name)
    if lora_name:
        store.register(folder_paths.get_full_path_or_raise("loras", lora_name), Grant(
            grant_id=f"licence:{licence_for(lora_name)['licence']}", asset_digest="",
            kind="model_licence", terms=licence_for(lora_name),
            signer_spki="huggingface:model-author"), label=lora_name)

    # A paid advertisement is a commercial use. That single fact is what the
    # CC-BY-NC LoRA collides with -- no fabricated territory restriction needed.
    ctx = {"production": "Campaign-482", "territory": "US",
           "channels": ["paid-social"], "release_start": "2026-08-01",
           "release_end": "2027-02-01",
           "commercial": not args.non_commercial,
           "intended_uses": ["advertising"]}
    gate = HermeticGate(store, media_licence_policy(), ctx)
    signer = SoftwareMeasurementSigner()
    print(f"purpose:    {'INTERNAL / non-commercial' if args.non_commercial else 'COMMERCIAL (paid advertisement)'}")
    for name in filter(None, (ckpt_name, lora_name)):
        t = licence_for(name)
        print(f"  {name[:44]:44} {t['licence']:26} commercial={t.get('commercial_use')}")

    banner("RENDERING THROUGH THE GATE")
    t0 = time.perf_counter()
    try:
        with covenant_gate(gate):
            model, clip, vae = nodes.CheckpointLoaderSimple().load_checkpoint(ckpt_name)
            if lora_name:
                model, clip = nodes.LoraLoader().load_lora(model, clip, lora_name, 1.0, 1.0)
            pos = nodes.CLIPTextEncode().encode(clip, args.prompt)[0]
            neg = nodes.CLIPTextEncode().encode(clip, "text, watermark, blurry")[0]
            latent = nodes.EmptyLatentImage().generate(args.size, args.size, 1)[0]
            samples = nodes.KSampler().sample(
                model, args.seed, args.steps, 7.0, "euler", "normal",
                pos, neg, latent)[0]
            image = nodes.VAEDecode().decode(vae, samples)[0]
    except ComfyGateRefusal as exc:
        print(f"\n  [BLOCKED] {exc}")
        print("\n  The render stopped. No master was produced, so there is nothing")
        print("  to covenant -- which is the entire point: the gate refuses at the")
        print("  moment of use, not in a report afterwards.")
        return 0
    dt = time.perf_counter() - t0
    print(f"  render OK in {dt:.1f}s")

    # --- write the master ourselves so the covenanted bytes are exact -------
    import numpy as np
    from PIL import Image
    arr = (image[0].detach().cpu().numpy() * 255.0).clip(0, 255).astype(np.uint8)
    master = HERE / "out" / f"final-ad-seed{args.seed}.png"
    master.parent.mkdir(exist_ok=True)
    Image.fromarray(arr).save(master)  # no ComfyUI metadata: we control the bytes
    print(f"  master: {master}  ({master.stat().st_size:,} bytes)")

    banner("INGREDIENTS RECORDED FROM ACTUAL LOADS")
    for i in gate.ingredients:
        print(f"  {i.role:12} {i.label[:40]:40} {i.grant_id}")

    tsa_clients = []
    if not args.no_anchor:
        from smoke_trust.audit.anchor import (
            DEFAULT_COMMERCIAL_TSA_URL, DEFAULT_SIGSTORE_TSA_URL, TSAClient)
        tsa_clients = [TSAClient(DEFAULT_COMMERCIAL_TSA_URL),
                       TSAClient(DEFAULT_SIGSTORE_TSA_URL)]

    cov, ingredients = issue(
        gate, str(master), signer=signer,
        renderer_identity={"engine": "ComfyUI", "commit": "806e092",
                           "sampler": "euler", "steps": args.steps, "seed": args.seed},
        tsa_clients=tsa_clients,
    )
    print(f"\n  master digest    {cov.master_digest}")
    print(f"  ingredient root  {cov.ingredient_root}")
    print(f"  hermetic         {cov.body['decision']['hermetic']}")

    banner("DISTRIBUTOR VERIFIES OFFLINE")
    report = verify(cov, str(master), signer.public_key(),
                    require_anchor=not args.no_anchor)
    print("  [OK] signature and master digest verify -- no contact with the studio")
    for w in report["witnesses"]:
        print(f"  [{'OK' if w['valid'] else '!!'}] trusted time {w.get('gen_time')} "
              f"from {w['url']}"
              + ("" if w.get("signature_checked") else "  (TSA sig UNVERIFIED: no pinned key)"))

    idx = 0
    record, path = prove_ingredient(ingredients, idx)
    verify_ingredient(record, path, idx, len(ingredients), cov.ingredient_root)
    print(f"  [OK] proved {record['grant_id']} was an input via {len(path)} sibling hashes")
    print(f"       the other {len(ingredients) - 1} ingredient(s) stay sealed")

    banner("ONE BYTE CHANGES")
    b = bytearray(master.read_bytes())
    b[-1] ^= 0x01
    tampered = master.with_name("tampered.png")
    tampered.write_bytes(bytes(b))
    try:
        verify(cov, str(tampered), signer.public_key())
        print("  [!!] verified a tampered master -- BUG")
        return 1
    except CovenantInvalid as exc:
        print(f"  [BLOCKED] {exc}")

    import json
    cov_path = master.with_suffix(".covenant.json")
    cov_path.write_text(json.dumps(cov.to_dict(), indent=2))
    print(f"\n  covenant: {cov_path}")
    print("\n  --lora dmd2            CC-BY-NC in a paid ad -> BLOCKED on a real load")
    print("  --lora dmd2 --non-commercial   same asset, internal use -> admitted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
