"""Vendored primitives — canonical JSON and smoke-merkle-v0.

WHY THIS FILE EXISTS. The covenant package must be extractable to a standalone
public repo, and it depended on `smoke_trust` for four things. `asn1.py` and
`rfc3161.py` are self-contained and were copied wholesale (see siblings). The
other two live inside large modules that drag in unrelated dependencies, so only
the needed functions are reproduced here.

THE RISK THIS CREATES, AND HOW IT IS MANAGED. A hand-extracted copy can drift
from — or silently differ from — its original, and a canonicalizer or a Merkle
root that differs by one byte produces covenants nobody else can verify. That is
the worst possible failure for this package: it would not error, it would quietly
emit unverifiable evidence.

So `covenant/test_vendor_conformance.py` asserts BYTE-FOR-BYTE agreement between
everything here and the smoke_trust originals, across randomized inputs, and is
run in-tree where both are present. If this file ever drifts, that test fails
before anything ships.

SOURCES (originals, at the time of extraction):
  canonical_json_bytes  <- smoke_trust/iea/contract.py
  _LEAF_TAG/_NODE_TAG,
  merkle_root_v0,
  merkle_proof_v0,
  verify_inclusion_v0   <- smoke_trust/attestation/windowed.py
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, List, Mapping, Sequence


class CanonicalError(ValueError):
    """Value is not canonical-JSON encodable (mirrors IeaContractError)."""


class MerkleError(ValueError):
    """Malformed Merkle input (mirrors AttestationError)."""


def canonical_json_bytes(value: Any) -> bytes:
    """Canonical bytes for the v0 contracts.

    Floats are forbidden so cross-language implementations do not have to
    reproduce language-specific number rendering.
    """

    def reject_float(item: Any) -> None:
        if isinstance(item, float):
            raise CanonicalError("floating-point values are forbidden in contracts")
        if isinstance(item, Mapping):
            for key, child in item.items():
                if not isinstance(key, str):
                    raise CanonicalError("object keys must be strings")
                reject_float(child)
        elif isinstance(item, (list, tuple)):
            for child in item:
                reject_float(child)

    reject_float(value)
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CanonicalError(f"value is not canonical-JSON encodable: {exc}") from exc


# --- smoke-merkle-v0 ---------------------------------------------------------
# RFC 6962-style domain separation. The tags are load-bearing: without them a
# leaf and an internal node are indistinguishable and a second-preimage attack
# lets a 3-leaf tree be presented as a 2-leaf tree with the same root.

_LEAF_TAG = b"\x00"
_NODE_TAG = b"\x01"


def _leaf_node(leaf: bytes) -> bytes:
    if not isinstance(leaf, (bytes, bytearray)) or len(leaf) != 32:
        raise MerkleError("merkle leaf must be 32 bytes")
    return hashlib.sha256(_LEAF_TAG + bytes(leaf)).digest()


def merkle_root_v0(leaves: Sequence[bytes]) -> bytes:
    """Merkle root over 32-byte leaves (domain-tagged, pairwise, odd-promote).

    Empty input is refused: an anchor over nothing proves nothing (fail-closed).
    A single leaf's root is its TAGGED leaf node, never the raw leaf — otherwise
    a raw entry hash would double as a root.
    """
    if not leaves:
        raise MerkleError("refusing to build a Merkle root over zero leaves")
    level = [_leaf_node(l) for l in leaves]
    while len(level) > 1:
        nxt = []
        for i in range(0, len(level) - 1, 2):
            nxt.append(hashlib.sha256(_NODE_TAG + level[i] + level[i + 1]).digest())
        if len(level) % 2 == 1:
            nxt.append(level[-1])  # odd node promoted unchanged
        level = nxt
    return level[0]


def merkle_proof_v0(leaves: Sequence[bytes], index: int) -> List[bytes]:
    """Sibling hashes (bottom-up) proving leaves[index] is under the root.

    Sides are NOT part of the proof — verify_inclusion_v0 derives the tree shape
    from (index, count), so a prover cannot choose sibling placement.
    """
    n = len(leaves)
    if not 0 <= index < n:
        raise MerkleError(f"proof index {index} out of range [0, {n})")
    level = [_leaf_node(l) for l in leaves]
    pos = index
    siblings: List[bytes] = []
    while len(level) > 1:
        if pos % 2 == 0:
            if pos + 1 < len(level):
                siblings.append(level[pos + 1])
        else:
            siblings.append(level[pos - 1])
        nxt = []
        for i in range(0, len(level) - 1, 2):
            nxt.append(hashlib.sha256(_NODE_TAG + level[i] + level[i + 1]).digest())
        if len(level) % 2 == 1:
            nxt.append(level[-1])
        level = nxt
        pos //= 2
    return siblings


def verify_inclusion_v0(
    root: bytes, leaf: bytes, index: int, count: int, siblings: Sequence[bytes]
) -> bool:
    """True iff `leaf` is leaf `index` of a `count`-leaf smoke-merkle-v0 tree with
    root `root`, given the bottom-up sibling hashes. The verifier derives the tree
    shape from (index, count) itself and requires the proof be exactly consumed —
    surplus or missing siblings fail."""
    if count < 1 or not 0 <= index < count:
        return False
    try:
        node = _leaf_node(leaf)
    except MerkleError:
        return False
    pos, width = index, count
    k = 0
    while width > 1:
        if pos % 2 == 0:
            if pos + 1 < width:
                if k >= len(siblings):
                    return False
                node = hashlib.sha256(_NODE_TAG + node + siblings[k]).digest()
                k += 1
            # else: odd node promoted, no sibling at this level
        else:
            if k >= len(siblings):
                return False
            node = hashlib.sha256(_NODE_TAG + siblings[k] + node).digest()
            k += 1
        pos //= 2
        width = (width + 1) // 2
    return k == len(siblings) and node == root
