"""Swapped bytes must not be attested as an approved asset.

`admit(path)` followed by a separate `open(path)` hashes one file and reads
another if the content is replaced in between. The covenant would then attest the
APPROVED digest while the renderer consumed different bytes -- and because a
covenant is signed, RFC 3161 timestamped and offline-verifiable, that is
authenticated FALSE evidence, strictly worse than issuing none.

The old memo made it durable rather than momentary: it was keyed on
(abspath, st_size, st_mtime_ns), and all three are attacker-settable (os.utime
sets mtime_ns exactly; padding matches a size), so ONE poisoning was reused for
the whole render.

This test needs neither ComfyUI nor smoke_trust -- only smoke_covenant's own
gate -- so it runs anywhere with this package's dependencies installed.

Run:
  python test_toctou.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import bootstrap_suite  # noqa: E402

# No smoke_trust dependency here, so don't require a suite root -- this must
# keep working in the standalone public repo, where there is none.
bootstrap_suite(need_suite=False)

from smoke_covenant import (  # noqa: E402
    AssetStore, Grant, HermeticGate, toy_territory_window_policy,
)

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"\n         {detail}" if detail else ""))
    if not ok:
        failures.append(name)


def _write(p: Path, data: bytes) -> Path:
    p.write_bytes(data)
    return p


def _gate(tmp: Path):
    asset = tmp / "licensed.bin"
    asset.write_bytes(b"LICENSED CONTENT")
    store = AssetStore()
    store.register(asset, Grant(
        grant_id="asset_license_774", asset_digest="", kind="asset_license",
        terms={"territories": ["US"], "expires_on": "2028-01-01"},
        signer_spki="demo"), label="licensed.bin")
    ctx = {"territory": "US", "channels": ["paid-social"], "release_end": "2027-02-01"}
    return HermeticGate(store, toy_territory_window_policy(), ctx), asset


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="covenant-toctou-"))
    print("=" * 74)
    print("TOCTOU between the gate's hash and the renderer's read")
    print("=" * 74)

    # 1. THE REAL FIX: admit_staged copies, so later mutation of the source
    #    cannot change what was admitted or what the renderer reads.
    stage = tmp / "_stage"
    gate, asset = _gate(tmp)
    gate._staging_dir = stage
    staged, digest = gate.admit_staged(asset, "photograph")

    with open(asset, "wb") as w:               # in-place overwrite of the SOURCE
        w.write(b"UNLICENSED CONTENT!!")

    check("staged copy still holds the admitted bytes",
          staged.read_bytes() == b"LICENSED CONTENT",
          f"got {staged.read_bytes()!r}")
    check("staged path is content-addressed by the admitted digest",
          staged.parent.name == digest and staged.name == asset.name,
          "<staging>/<digest>/<original name>: content-addressed, but the "
          "basename still reads as the model so logs stay legible")
    check("recorded digest matches the staged bytes",
          gate.ingredients and gate.ingredients[0].asset_digest == digest)

    # 2. admit_open: honest about what it does and does not cover.
    #    In-place overwrite keeps the inode, so a held handle follows it -- this
    #    is a PARTIAL mitigation and the test says so rather than pretending.
    tmp2 = Path(tempfile.mkdtemp(prefix="covenant-fd-"))
    gate2, asset2 = _gate(tmp2)
    fh, _ = gate2.admit_open(asset2, "photograph")
    blocked_by_os = False
    try:
        os.replace(_write(tmp2 / "other.bin", b"REPLACED BYTES!!"), asset2)
    except PermissionError:
        # Windows refuses to replace a file that has an open handle. That is a
        # STRONGER guarantee than POSIX gives: the OS blocks the swap outright
        # rather than leaving the handle pointed at the old inode. Either outcome
        # is a pass; recording which one happened keeps the test honest about
        # being platform-dependent.
        blocked_by_os = True
    read_back = fh.read()
    fh.close()
    check("admit_open survives PATH REPLACEMENT (new inode)",
          read_back == b"LICENSED CONTENT",
          ("the OS refused the replacement outright (Windows)" if blocked_by_os
           else f"handle kept the old inode (POSIX); got {read_back!r}"))

    # 3. The memo must not be poisoned across a path replacement even when the
    #    attacker reproduces size and mtime exactly (the old key's three fields).
    tmp3 = Path(tempfile.mkdtemp(prefix="covenant-memo-"))
    gate3, asset3 = _gate(tmp3)
    st_before = os.stat(asset3)
    d1 = gate3._digest(asset3)

    evil = _write(tmp3 / "evil.bin", b"EVIL CONTENT!!!!")   # same length
    os.utime(evil, ns=(st_before.st_atime_ns, st_before.st_mtime_ns))
    os.replace(evil, asset3)
    st_after = os.stat(asset3)
    check("attacker reproduced size and mtime exactly",
          st_after.st_size == st_before.st_size
          and st_after.st_mtime_ns == st_before.st_mtime_ns,
          "the OLD (path,size,mtime) key would have hit and served a stale digest")

    d2 = gate3._digest(asset3)
    check("inode-keyed memo does NOT serve a stale digest after replacement",
          d1 != d2, f"d1={d1[:16]}... d2={d2[:16]}...")

    # 3. The replaced file no longer resolves to the grant -- it is refused.
    from smoke_covenant import AssetNotRegistered
    gate3, asset3 = _gate(Path(tempfile.mkdtemp(prefix="covenant-refuse-")))
    with open(asset3, "wb") as w:
        w.write(b"SOMETHING ELSE!!")
    refused = False
    try:
        gate3.admit(asset3, "photograph")
    except AssetNotRegistered:
        refused = True
    check("swapped content is refused, not silently attested", refused)

    print("=" * 74)
    if failures:
        print(f"FAILED: {len(failures)} -> {', '.join(failures)}")
        return 1
    print("Hash-and-use is bound to one descriptor; the memo is inode-keyed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
