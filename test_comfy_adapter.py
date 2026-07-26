"""Exercise the ComfyUI adapter against the REAL ComfyUI folder_paths module.

Uses stand-in weight files rather than multi-GB checkpoints: the thing under test
is whether the hook fires and refuses, which does not depend on the bytes being a
real model. Real weights get exercised by the end-to-end render demo.

Run:
  COMFY=C:/Users/topdy/ComfyUI
  $COMFY/.venv/Scripts/python.exe covenant/test_comfy_adapter.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

COMFY = Path(os.environ.get("COMFYUI_ROOT", r"C:/Users/topdy/ComfyUI"))
HERE = Path(__file__).resolve().parent
SUITE = HERE.parents[0]
sys.path.insert(0, str(COMFY))
sys.path.insert(0, str(SUITE / "trust"))
sys.path.insert(0, str(SUITE / "sdks" / "agent" / "python"))
sys.path.insert(0, str(HERE))

import folder_paths  # noqa: E402  (real ComfyUI)

from smoke_covenant import AssetStore, Grant, HermeticGate, toy_territory_window_policy  # noqa: E402
from smoke_covenant.adapters.comfy import ComfyGateRefusal, covenant_gate  # noqa: E402

PASS, FAIL = "  [PASS]", "  [FAIL]"
failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{PASS if ok else FAIL} {name}" + (f"\n         {detail}" if detail else ""))
    if not ok:
        failures.append(name)


def main() -> int:
    lora_dir = Path(folder_paths.get_folder_paths("loras")[0])
    lora_dir.mkdir(parents=True, exist_ok=True)
    granted = lora_dir / "covenant_test_granted.safetensors"
    ungranted = lora_dir / "covenant_test_ungranted.safetensors"
    granted.write_bytes(b"<stand-in for a licensed LoRA>")
    ungranted.write_bytes(b"<stand-in for a scraped LoRA>")
    folder_paths.cache_helper.clear()

    store = AssetStore()
    store.register(granted, Grant(
        grant_id="model_grant_204", asset_digest="", kind="model_grant",
        terms={"territories": ["US"], "expires_on": "2028-01-01"},
        signer_spki="demo-brand"), label=granted.name)

    ctx = {"territory": "US", "channels": ["paid-social"], "release_end": "2027-02-01"}

    print("=" * 72)
    print(f"ComfyUI adapter vs real folder_paths  ({COMFY})")
    print("=" * 72)

    # 1. granted asset passes and is recorded with the folder name as its role
    gate = HermeticGate(store, toy_territory_window_policy(), ctx)
    with covenant_gate(gate):
        p = folder_paths.get_full_path("loras", granted.name)
    check("granted LoRA resolves", p is not None and Path(p).name == granted.name)
    check("recorded exactly 1 ingredient", len(gate.ingredients) == 1,
          f"got {len(gate.ingredients)}")
    if gate.ingredients:
        ing = gate.ingredients[0]
        check("role derived from folder_name", ing.role == "lora", f"role={ing.role}")
        check("bound to the right grant", ing.grant_id == "model_grant_204")

    # 2. ungranted asset is refused -- the money shot, inside real ComfyUI
    gate2 = HermeticGate(store, toy_territory_window_policy(), ctx)
    refused = False
    with covenant_gate(gate2):
        try:
            folder_paths.get_full_path("loras", ungranted.name)
        except ComfyGateRefusal as exc:
            refused = True
            msg = str(exc).splitlines()[0]
    check("ungranted LoRA is REFUSED", refused, msg if refused else "it was admitted")
    check("nothing recorded for the refused render", len(gate2.ingredients) == 0)
    check("refusal captured for the operator", len(gate2.refusals) == 1)

    # 3. get_full_path_or_raise routes through the SAME single patch
    gate3 = HermeticGate(store, toy_territory_window_policy(), ctx)
    with covenant_gate(gate3):
        folder_paths.get_full_path_or_raise("loras", granted.name)
    check("get_full_path_or_raise covered by one patch", len(gate3.ingredients) == 1,
          f"got {len(gate3.ingredients)} (2 would mean double-recording)")

    # 4. a missing file must pass through as None, not be treated as an asset
    gate4 = HermeticGate(store, toy_territory_window_policy(), ctx)
    with covenant_gate(gate4):
        missing = folder_paths.get_full_path("loras", "does_not_exist_xyz.safetensors")
    check("missing file returns None, records nothing",
          missing is None and len(gate4.ingredients) == 0)

    # 5. record_only admits but marks the render non-hermetic
    gate5 = HermeticGate(store, toy_territory_window_policy(), ctx, strict=False)
    with covenant_gate(gate5, record_only=True):
        got = folder_paths.get_full_path("loras", ungranted.name)
    check("record_only admits the ungranted asset", got is not None)
    check("record_only marks the render NON-hermetic", gate5.hermetic is False)

    # 6. patches are fully removed on exit
    check("get_full_path restored", folder_paths.get_full_path.__name__ == "get_full_path",
          f"still {folder_paths.get_full_path.__name__}")
    check("get_annotated_filepath restored",
          folder_paths.get_annotated_filepath.__name__ == "get_annotated_filepath")

    for f in (granted, ungranted):
        f.unlink(missing_ok=True)
    folder_paths.cache_helper.clear()

    print("=" * 72)
    if failures:
        print(f"FAILED: {len(failures)} -> {', '.join(failures)}")
        return 1
    print("All adapter checks passed against real ComfyUI folder_paths.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
