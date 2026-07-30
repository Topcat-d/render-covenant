"""Close the nodes.py:629 coverage gap: do textual-inversion embeddings cross the gate?

Before _EmbedHook this was an invalidating hole. nodes.py:629 hands the checkpoint
loader `embedding_directory=folder_paths.get_folder_paths("embeddings")` -- a
DIRECTORY LIST -- so embeddings resolve inside comfy.sd1_clip.load_embed and never
touch get_full_path. A render using one would report hermetic:True while an
unlicensed asset had in fact participated.

ComfyUI is auto-detected (see _paths.py); set COMFYUI_ROOT to override.

Run (no checkpoint needed -- this exercises load_embed directly):
  "$COMFYUI_ROOT/.venv/Scripts/python.exe" test_embedding_gap.py
"""

from __future__ import annotations

import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import bootstrap_comfy, find_comfyui_root, skip_or_die  # noqa: E402

# See test_comfy_adapter.py for why this probe exists: standalone stays
# fail-closed via bootstrap_comfy()'s own SystemExit; pytest gets a clean
# module-level skip instead of a collection crash.
if find_comfyui_root() is None and "pytest" in sys.modules:
    skip_or_die(
        "no ComfyUI checkout found (see _paths.py) -- set COMFYUI_ROOT to "
        "run this test against a real ComfyUI install",
        exit_code=1,
    )

COMFY, SUITE = bootstrap_comfy()

import folder_paths  # noqa: E402

# torch + safetensors are NOT part of this repo's own dependencies (ComfyUI
# supplies its own CUDA-matched torch; see pyproject.toml's `comfy` extra) --
# only ComfyUI's own venv is guaranteed to have them. Standalone keeps the
# plain ImportError/traceback it always had; under pytest that would be an
# ERROR rather than a SKIP, which misrepresents an environment gap as a
# failure, so route it through the same skip path as the ComfyUI probe.
try:
    import torch  # noqa: E402
    from safetensors.torch import save_file  # noqa: E402
except ImportError as exc:
    if "pytest" in sys.modules:
        skip_or_die(f"torch/safetensors not available in this environment: {exc}",
                     exit_code=1)
    raise

from smoke_covenant import AssetStore, Grant, HermeticGate  # noqa: E402
from smoke_covenant.adapters.comfy import ComfyGateRefusal, covenant_gate  # noqa: E402
from smoke_covenant.policies import LICENCE_TERMS, media_licence_policy  # noqa: E402

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"\n         {detail}" if detail else ""))
    if not ok:
        failures.append(name)


def main() -> int:
    import comfy.sd1_clip as sd1

    emb_dir = Path(folder_paths.get_folder_paths("embeddings")[0])
    emb_dir.mkdir(parents=True, exist_ok=True)
    granted = emb_dir / "covenant_ok_embed.safetensors"
    ungranted = emb_dir / "covenant_nc_embed.safetensors"
    # Distinct CONTENT, not just distinct names: the store is content-addressed,
    # so identical bytes are the same asset however they are filed. (Writing both
    # as torch.zeros made them one asset and the second grant clobbered the first
    # -- a real property, caught by getting the fixture wrong.)
    save_file({"emb_params": torch.full((1, 768), 0.11)}, str(granted))
    save_file({"emb_params": torch.full((1, 768), 0.22)}, str(ungranted))

    store = AssetStore()
    store.register(granted, Grant(
        grant_id="licence:CreativeML OpenRAIL-M", asset_digest="", kind="model_licence",
        terms=LICENCE_TERMS["creativeml-openrail-m"],
        signer_spki="huggingface:embed-author"), label=granted.name)
    store.register(ungranted, Grant(
        grant_id="licence:CC-BY-NC-4.0", asset_digest="", kind="model_licence",
        terms=LICENCE_TERMS["cc-by-nc-4.0"],
        signer_spki="huggingface:embed-author"), label=ungranted.name)

    ctx = {"territory": "US", "channels": ["paid-social"], "release_end": "2027-02-01",
           "commercial": True, "intended_uses": ["advertising"]}
    dirs = folder_paths.get_folder_paths("embeddings")

    print("=" * 74)
    print("nodes.py:629 embedding coverage gap")
    print("=" * 74)

    # 0. the gap itself: without the hook, load_embed never touches the gate
    gate0 = HermeticGate(store, media_licence_policy(), ctx)
    sd1.load_embed(granted.stem, dirs, 768, "clip_l")
    check("WITHOUT the hook an embedding loads unseen (the original gap)",
          len(gate0.ingredients) == 0)

    # 1. permitted embedding is admitted and recorded as role=embedding
    gate1 = HermeticGate(store, media_licence_policy(), ctx)
    with covenant_gate(gate1):
        out = sd1.load_embed(granted.stem, dirs, 768, "clip_l")
    check("permitted embedding loads", out is not None)
    check("recorded as an ingredient", len(gate1.ingredients) == 1,
          f"got {len(gate1.ingredients)}")
    if gate1.ingredients:
        check("role is 'embedding'", gate1.ingredients[0].role == "embedding",
              f"role={gate1.ingredients[0].role}")

    # 2. THE FIX: a CC-BY-NC embedding in a commercial render is now BLOCKED
    gate2 = HermeticGate(store, media_licence_policy(), ctx)
    refused = False
    msg = ""
    with covenant_gate(gate2):
        try:
            sd1.load_embed(ungranted.stem, dirs, 768, "clip_l")
        except ComfyGateRefusal as exc:
            refused, msg = True, str(exc).splitlines()[0]
    check("CC-BY-NC embedding is REFUSED in a paid ad", refused,
          msg if refused else "it loaded -- the gap is still open")

    # 3. an unregistered embedding is refused too
    stray = emb_dir / "covenant_stray_embed.safetensors"
    save_file({"emb_params": torch.full((1, 768), 0.33)}, str(stray))
    gate3 = HermeticGate(store, media_licence_policy(), ctx)
    stray_refused = False
    with covenant_gate(gate3):
        try:
            sd1.load_embed(stray.stem, dirs, 768, "clip_l")
        except ComfyGateRefusal:
            stray_refused = True
    check("unregistered embedding is REFUSED", stray_refused)

    # 4. a name that resolves to nothing must not be treated as an asset
    gate4 = HermeticGate(store, media_licence_policy(), ctx)
    with covenant_gate(gate4):
        missing = sd1.load_embed("covenant_no_such_embed_xyz", dirs, 768, "clip_l")
    check("missing embedding records nothing",
          missing is None and len(gate4.ingredients) == 0)

    # 5. the patch is fully removed on exit
    check("load_embed restored", sd1.load_embed.__name__ == "load_embed",
          f"still {sd1.load_embed.__name__}")

    for f in (granted, ungranted, stray):
        f.unlink(missing_ok=True)

    print("=" * 74)
    if failures:
        print(f"FAILED: {len(failures)} -> {', '.join(failures)}")
        return 1
    print("Embedding gap CLOSED. Mirror drift fails closed (see _EmbedHook).")
    return 0


def test_main():
    # See test_comfy_adapter.py's test_main(): main() imports real ComfyUI's
    # comfy.sd1_clip directly, which needs ComfyUI's own heavy deps (e.g.
    # transformers) -- not this test venv's, even once torch/safetensors
    # (guarded above) are present. An environment gap, not a real failure.
    try:
        result = main()
    except ImportError as exc:
        import pytest
        pytest.skip(
            f"a ComfyUI-side dependency is unavailable in this environment "
            f"(run with ComfyUI's own venv instead): {exc}"
        )
    assert result == 0


if __name__ == "__main__":
    raise SystemExit(main())
