"""The hermetic render gate: resolve-or-refuse on every asset read.

This is the enforcement boundary. It is the reason a covenant means anything
more than a spreadsheet: the ingredient list is built from what the renderer
ACTUALLY READ, not from what someone declared.

    lineage from declarations  ->  a signed spreadsheet
    lineage from reads through a gate  ->  a hermetic build

The analogy is exact. A hermetic software build cannot secretly import an
undeclared dependency because the environment refuses to supply one. Here the
render cannot secretly use an unlicensed asset because the gate refuses to open
it.

BOUNDARY OF THE CLAIM (premise 4 — coverage is part of the theorem):
A bypass outside this gate INVALIDATES the covenant, it does not weaken it. If
the renderer can open files by a path the gate does not mediate, the covenant
proves nothing about those bytes. `strict=True` is the only mode in which the
lineage claim holds; `strict=False` exists for incremental adoption and marks
the manifest `hermetic: false` so a verifier can see it.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from smoke_trust.iea.contract import canonical_json_bytes

from .grants import (
    AssetNotRegistered,
    AssetStore,
    CovenantError,
    Grant,
    Policy,
    PolicyDenied,
    digest_file,
)

INGREDIENT_LEAF_DOMAIN = b"SMOKE-COVENANT-INGREDIENT-V0\x00"


@dataclass(frozen=True)
class Ingredient:
    """One asset that actually crossed the gate, with the decision that let it."""

    asset_digest: str
    grant_id: str
    grant_kind: str
    role: str
    signer_spki: str | None
    label: str

    def record(self) -> dict:
        return {
            "asset_digest": self.asset_digest,
            "grant_id": self.grant_id,
            "grant_kind": self.grant_kind,
            "role": self.role,
            "signer_spki": self.signer_spki,
        }

    def leaf(self) -> bytes:
        """32-byte domain-separated leaf for the ingredient Merkle tree.

        `label` is deliberately EXCLUDED — it is a human convenience and must not
        change the root, or two studios with different filenames would compute
        different covenants for identical rights facts.
        """
        return hashlib.sha256(
            INGREDIENT_LEAF_DOMAIN + canonical_json_bytes(self.record())
        ).digest()


@dataclass
class GateDecision:
    """A refusal, kept for the operator. Refusals are the product's whole point,
    so they are recorded rather than raised-and-forgotten."""

    path: str
    asset_digest: str
    reason: str
    error: str


class HermeticGate:
    """Mediates asset access for one render.

    Usage is deliberately narrow: `open_asset` is the ONLY way in. Anything that
    wants to read an ingredient goes through it or is outside the theorem.
    """

    def __init__(
        self,
        store: AssetStore,
        policy: Policy,
        context: Mapping[str, object],
        *,
        strict: bool = True,
    ) -> None:
        self._store = store
        self._policy = policy
        self._context = dict(context)
        self._strict = strict
        self._ingredients: dict[tuple[str, str], Ingredient] = {}
        self._refusals: list[GateDecision] = []
        self._escapes: list[str] = []
        self._digests: dict[tuple[str, int, int], str] = {}

    # --- the rule -----------------------------------------------------------

    def _digest(self, path: str | Path) -> str:
        """Hash `path`, memoized on (abspath, size, mtime_ns) for this render.

        The memo lives HERE and not in an adapter on purpose. A renderer resolves
        the same checkpoint several times per run, and a 6 GB model hashed on each
        resolution is tens of GB of SHA-256. The obvious alternative -- letting the
        caller pass a precomputed digest -- would move the identity decision
        outside the gate, so a buggy adapter could make the gate admit bytes it
        never hashed. The gate stays authoritative for identity AND the rule.

        Tradeoff, stated because it is a real one: a file mutated in place with an
        identical size and mtime_ns would not be re-hashed within one render's
        lifetime. That is the standard build-system bargain and the gate is
        render-scoped, so the window is a single render.
        """
        p = Path(path)
        st = p.stat()
        key = (str(p.resolve()), st.st_size, st.st_mtime_ns)
        if key not in self._digests:
            self._digests[key] = digest_file(p)
        return self._digests[key]

    def admit(self, path: str | Path, role: str) -> str:
        """Resolve a file to a grant, evaluate policy, record it. Returns the digest.

        Raises AssetNotRegistered or PolicyDenied. There is no permissive path and
        no silent fallback: a refusal here is the gate working, not failing.
        """
        digest = self._digest(path)
        try:
            grant: Grant = self._store.resolve(digest)
            self._policy.evaluate(grant, self._context)
        except (AssetNotRegistered, PolicyDenied) as exc:
            self._refusals.append(
                GateDecision(
                    path=str(path),
                    asset_digest=digest,
                    reason=type(exc).__name__,
                    error=str(exc),
                )
            )
            raise

        ing = Ingredient(
            asset_digest=digest,
            grant_id=grant.grant_id,
            grant_kind=grant.kind,
            role=role,
            signer_spki=grant.signer_spki,
            label=self._store.label(digest),
        )
        self._ingredients[(digest, role)] = ing
        return digest

    def open_asset(self, path: str | Path, role: str, mode: str = "rb"):
        """admit() then open. The only sanctioned way to read an ingredient."""
        self.admit(path, role)
        return open(path, mode)

    def note_escape(self, path: str | Path) -> None:
        """Record that something read bytes WITHOUT crossing the gate.

        In strict mode this raises: an unmediated read means the hermetic claim is
        false, and a covenant that quietly tolerates one is worse than no covenant.
        """
        if self._strict:
            raise CovenantError(
                f"unmediated asset read: {path} -- the render escaped the gate, so the "
                "lineage is incomplete and no covenant can be issued (strict mode)"
            )
        self._escapes.append(str(path))

    # --- what the covenant commits to ---------------------------------------

    @property
    def ingredients(self) -> list[Ingredient]:
        """Canonically ORDERED ingredients.

        Sorted by leaf bytes, not by read order. Read scheduling is not a rights
        fact, and sorting makes the root reproducible across runs and machines —
        which is what lets a distributor recompute it.
        """
        return sorted(self._ingredients.values(), key=lambda i: i.leaf())

    @property
    def refusals(self) -> list[GateDecision]:
        return list(self._refusals)

    @property
    def hermetic(self) -> bool:
        return self._strict and not self._escapes

    @property
    def context(self) -> dict:
        return dict(self._context)

    @property
    def policy(self) -> Policy:
        return self._policy
