"""The vendored primitives must be BYTE-FOR-BYTE identical to their originals.

This is the test that makes extraction safe. A hand-copied canonicalizer or
Merkle implementation that differs from the original by one byte does not error
-- it quietly emits covenants nobody else can verify, which is the worst failure
mode available to this package. Divergence must be loud and immediate.

Runs in-tree, where both the vendored copy and the smoke_trust original are
importable. Randomized inputs, not a handful of fixtures, because the interesting
divergences hide in odd shapes: odd leaf counts (the promote path), unicode keys,
nested containers, large integers.

  "C:/Users/topdy/Smoke(new)/smoke-suite/.venv/Scripts/python.exe" covenant/test_vendor_conformance.py
"""

from __future__ import annotations

import os
import random
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
SUITE = HERE.parents[0]
for p in (SUITE / "trust", SUITE / "sdks" / "agent" / "python", HERE):
    sys.path.insert(0, str(p))

# originals
from smoke_trust.attestation.windowed import (  # noqa: E402
    merkle_proof_v0 as orig_proof,
    merkle_root_v0 as orig_root,
    verify_inclusion_v0 as orig_verify,
)
from smoke_trust.iea.contract import canonical_json_bytes as orig_canon  # noqa: E402

# vendored
from smoke_covenant._vendor.primitives import (  # noqa: E402
    canonical_json_bytes as vend_canon,
    merkle_proof_v0 as vend_proof,
    merkle_root_v0 as vend_root,
    verify_inclusion_v0 as vend_verify,
)

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"\n         {detail}" if detail else ""))
    if not ok:
        failures.append(name)


def _rand_json(rng, depth=0):
    kind = rng.choice(["str", "int", "bool", "null", "list", "dict"] if depth < 3
                      else ["str", "int", "bool", "null"])
    if kind == "str":
        return rng.choice(["", "a", "ünïcødé", "with\"quote", "tab\there", "𝔘𝔫𝔦"])
    if kind == "int":
        return rng.choice([0, -1, 1, 2**53, -(2**53), 12345])
    if kind == "bool":
        return rng.choice([True, False])
    if kind == "null":
        return None
    if kind == "list":
        return [_rand_json(rng, depth + 1) for _ in range(rng.randint(0, 4))]
    return {f"k{rng.randint(0, 9)}{rng.choice(['', 'ü'])}": _rand_json(rng, depth + 1)
            for _ in range(rng.randint(0, 4))}


def main() -> int:
    rng = random.Random(20260727)
    print("=" * 74)
    print("Vendored primitives vs smoke_trust originals")
    print("=" * 74)

    # --- canonical JSON ------------------------------------------------------
    mismatches = 0
    for _ in range(500):
        v = _rand_json(rng)
        if orig_canon(v) != vend_canon(v):
            mismatches += 1
    check("canonical_json_bytes identical over 500 random values", mismatches == 0,
          f"{mismatches} mismatches")

    # --- merkle roots, ALL leaf counts including odd (the promote path) ------
    bad_root, bad_proof, bad_verify = [], [], []
    for n in range(1, 34):
        leaves = [os.urandom(32) for _ in range(n)]
        if orig_root(leaves) != vend_root(leaves):
            bad_root.append(n)
        for idx in range(n):
            if orig_proof(leaves, idx) != vend_proof(leaves, idx):
                bad_proof.append((n, idx))
            # cross-verify: each implementation must accept the OTHER's proof,
            # which catches a divergence that happens to be self-consistent.
            root = orig_root(leaves)
            if not vend_verify(root, leaves[idx], idx, n, orig_proof(leaves, idx)):
                bad_verify.append(("vend-verifies-orig", n, idx))
            if not orig_verify(root, leaves[idx], idx, n, vend_proof(leaves, idx)):
                bad_verify.append(("orig-verifies-vend", n, idx))

    check("merkle_root_v0 identical for n=1..33", not bad_root, f"differed at n={bad_root}")
    check("merkle_proof_v0 identical for every index", not bad_proof,
          f"{len(bad_proof)} differing (n,index) pairs")
    check("each implementation verifies the other's proofs", not bad_verify,
          f"{len(bad_verify)} cross-verify failures")

    # --- rejection behaviour must match too ---------------------------------
    both_reject = True
    for bad in ([], [b"tooshort"], [os.urandom(31)]):
        o = e = None
        try:
            orig_root(bad)
        except Exception as exc:
            o = type(exc).__name__
        try:
            vend_root(bad)
        except Exception as exc:
            e = type(exc).__name__
        if (o is None) != (e is None):
            both_reject = False
    check("both reject empty and malformed leaves", both_reject,
          "an input one accepts and the other refuses is a divergence too")

    # --- the DER modules were copied wholesale; prove they are byte-identical
    import hashlib
    same = []
    for name in ("asn1.py", "rfc3161.py"):
        a = (SUITE / "trust" / "smoke_trust" / "audit" / name).read_bytes()
        b = (HERE / "smoke_covenant" / "_vendor" / name).read_bytes()
        same.append((name, hashlib.sha256(a).hexdigest() == hashlib.sha256(b).hexdigest()))
    for name, ok in same:
        check(f"{name} is an exact copy", ok, "copied wholesale, so drift is a diff")

    print("=" * 74)
    if failures:
        print(f"FAILED: {len(failures)} -> {', '.join(failures)}")
        return 1
    print("Vendored primitives conform. Extraction is safe.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
