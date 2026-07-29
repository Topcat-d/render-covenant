"""Does the gate survive checkpoint-sized assets, and does the memo actually work?

The predicted failure was never the crypto: it was that ComfyUI resolves the same
model several times per run and the gate re-hashes it every time. A 6 GB
checkpoint resolved 5 times is 30 GB of SHA-256 if the memo is broken.

Generates a stand-in of realistic size rather than downloading weights -- the
thing under test is hashing cost and hook behaviour at scale, which does not
depend on the bytes being a real model.

ComfyUI is auto-detected (see _paths.py); set COMFYUI_ROOT to override.

Run:
  "$COMFYUI_ROOT/.venv/Scripts/python.exe" covenant/test_large_asset.py [GiB]
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import bootstrap_comfy  # noqa: E402

COMFY, SUITE = bootstrap_comfy()

import folder_paths  # noqa: E402

from smoke_covenant import AssetStore, Grant, HermeticGate, toy_territory_window_policy  # noqa: E402
from smoke_covenant.adapters.comfy import covenant_gate  # noqa: E402

GIB = 1 << 30
RESOLVES = 5


def main() -> int:
    size_gib = float(sys.argv[1]) if len(sys.argv) > 1 else 2.0
    nbytes = int(size_gib * GIB)

    lora_dir = Path(folder_paths.get_folder_paths("loras")[0])
    lora_dir.mkdir(parents=True, exist_ok=True)
    big = lora_dir / "covenant_bigasset_test.safetensors"

    import shutil
    free_gb = shutil.disk_usage(lora_dir).free / GIB
    print(f"free disk: {free_gb:.1f} GiB, need ~{size_gib:.1f} GiB")
    if free_gb < size_gib + 2:
        print("FAIL: not enough free disk; pass a smaller size argument")
        return 1

    print(f"writing {size_gib:.1f} GiB stand-in asset...", flush=True)
    t0 = time.perf_counter()
    chunk = os.urandom(1 << 20)
    with open(big, "wb") as fh:
        for _ in range(nbytes // len(chunk)):
            fh.write(chunk)
    print(f"  wrote in {time.perf_counter() - t0:.1f}s")
    folder_paths.cache_helper.clear()

    store = AssetStore()
    t0 = time.perf_counter()
    store.register(big, Grant(
        grant_id="model_grant_big", asset_digest="", kind="model_grant",
        terms={"territories": ["US"], "expires_on": "2028-01-01"},
        signer_spki="demo-brand"), label=big.name)
    reg_s = time.perf_counter() - t0
    print(f"\nregister (1 full hash): {reg_s:.2f}s = {size_gib / reg_s:.2f} GiB/s")

    ctx = {"territory": "US", "channels": ["paid-social"], "release_end": "2027-02-01"}
    gate = HermeticGate(store, toy_territory_window_policy(), ctx)

    timings = []
    with covenant_gate(gate):
        for i in range(RESOLVES):
            t0 = time.perf_counter()
            folder_paths.get_full_path("loras", big.name)
            timings.append(time.perf_counter() - t0)

    cold, warm = timings[0], timings[1:]
    print(f"resolve #1 (cold):      {cold:.2f}s")
    print(f"resolve #2-{RESOLVES} (memo):    " +
          ", ".join(f"{t * 1000:.1f}ms" for t in warm))

    naive = cold * RESOLVES
    actual = sum(timings)
    print(f"\nwithout memo would be:  {naive:.2f}s ({RESOLVES} full hashes)")
    print(f"actual:                 {actual:.2f}s")
    print(f"saved:                  {naive - actual:.2f}s "
          f"({(1 - actual / naive) * 100:.0f}%)")

    ok = True
    if len(gate.ingredients) != 1:
        print(f"FAIL: recorded {len(gate.ingredients)} ingredients, expected 1")
        ok = False
    if max(warm) > cold * 0.25:
        print(f"FAIL: memo not effective -- warm {max(warm):.2f}s vs cold {cold:.2f}s")
        ok = False

    big.unlink(missing_ok=True)
    folder_paths.cache_helper.clear()

    print("\n" + ("PASS: memo holds at checkpoint scale, 1 ingredient recorded."
                  if ok else "FAILED"))
    print(f"NOTE: {RESOLVES} resolutions is an assumption. How many times ComfyUI\n"
          f"      really resolves a model per run is UNCHECKED until a real render.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
