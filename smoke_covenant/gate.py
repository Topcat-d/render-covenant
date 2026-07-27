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
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from ._vendor.primitives import canonical_json_bytes

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
        staging_dir: str | Path | None = None,
    ) -> None:
        self._store = store
        self._policy = policy
        self._context = dict(context)
        self._strict = strict
        # Content-addressed staging. Must be a location the parties who can write
        # the asset store CANNOT write, or the copy inherits the same exposure.
        self._staging_dir = Path(staging_dir) if staging_dir else None
        self._ingredients: dict[tuple[str, str], Ingredient] = {}
        self._refusals: list[GateDecision] = []
        self._escapes: list[str] = []
        self._digests: dict[tuple[str, int, int], str] = {}
        self._admitted_paths: set[str] = set()

    # --- the rule -----------------------------------------------------------

    def _digest(self, path: str | Path) -> str:
        """Hash `path`, memoized on the OPEN FILE'S identity for this render.

        The memo lives HERE and not in an adapter on purpose. A renderer resolves
        the same checkpoint several times per run, and a 6 GB model hashed on each
        resolution is tens of GB of SHA-256. The obvious alternative -- letting the
        caller pass a precomputed digest -- would move the identity decision
        outside the gate, so a buggy adapter could make the gate admit bytes it
        never hashed. The gate stays authoritative for identity AND the rule.

        WHY THE KEY IS AN OPEN DESCRIPTOR AND NOT (path, size, mtime).
        The earlier key was (abspath, st_size, st_mtime_ns), and all three are
        attacker-settable: os.utime sets mtime_ns exactly and padding matches a
        size. So an attacker with write access to the asset directory could poison
        one entry and have the stale digest reused for the whole render. Keying on
        (st_dev, st_ino) READ FROM AN OPEN HANDLE removes the name from the
        identity entirely -- a replacement file is a different inode, so it misses
        the memo and gets hashed.

        This narrows the TOCTOU window but does NOT close it, and `admit_open` is
        the API that does. See its docstring.
        """
        with open(path, "rb") as fh:
            return self._digest_fh(fh)

    def _digest_fh(self, fh) -> str:
        """Hash from an already-open handle, memoized on (st_dev, st_ino).

        Hashing through the descriptor rather than re-opening by name means the
        bytes hashed are the bytes of THIS file object, whatever happens to the
        path afterwards.
        """
        st = os.fstat(fh.fileno())
        key = (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)
        if key not in self._digests:
            h = hashlib.sha256()
            fh.seek(0)
            while chunk := fh.read(1 << 20):
                h.update(chunk)
            fh.seek(0)
            self._digests[key] = h.hexdigest()
        return self._digests[key]

    def admit(self, path: str | Path, role: str) -> str:
        """Resolve a file to a grant, evaluate policy, record it. Returns the digest.

        Raises AssetNotRegistered or PolicyDenied. There is no permissive path and
        no silent fallback: a refusal here is the gate working, not failing.
        """
        return self._admit_digest(self._digest(path), path, role)

    def _admit_digest(self, digest: str, path: str | Path, role: str) -> str:
        """Resolve + evaluate + record for an ALREADY-COMPUTED digest.

        Split out so `admit_open` can hash from its own held descriptor and still
        run the identical rule. The digest is only ever produced inside this
        class, never accepted from a caller -- letting a caller supply one would
        move the identity decision outside the gate, which is the thing the gate
        exists to own.
        """
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

        # Recorded so the escape probe can adjudicate by PATH at teardown instead
        # of hashing inside its open() hook -- hashing there recursed into the
        # hook and flagged the gate's own admit-time read as an escape.
        self._admitted_paths.add(os.path.abspath(str(path)))

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

    def admit_staged(self, path: str | Path, role: str) -> tuple[Path, str]:
        """Copy into gate-owned storage, then admit the COPY. Returns (path, digest).

        THIS IS THE ONLY FULLY TOCTOU-FREE ENTRY POINT.

        Holding a descriptor (see `admit_open`) is not enough, and the reason is
        worth stating because it is easy to get wrong: opening a path "wb"
        TRUNCATES AND REWRITES THE SAME INODE. A held handle and an inode-keyed
        memo both follow that mutation, so an attacker who can write the file
        defeats them without ever replacing it. Descriptor-holding defends only
        against path REPLACEMENT (os.replace / unlink+create, which yields a new
        inode).

        Copying breaks the dependency entirely: the staged copy lives in a
        directory the renderer's asset-writers do not control, so the bytes hashed
        remain the bytes read. The digest is computed FROM THE COPY, so even a
        source mutated mid-copy yields a digest matching whatever was actually
        captured -- and if that no longer resolves to a grant, it is refused.

        Cost: one copy per distinct digest. Staging is content-addressed, so a
        6 GB checkpoint is copied once and reused by every later render and every
        later resolution within a render. Set `staging_dir` on the gate to control
        where that lives.
        """
        if self._staging_dir is None:
            raise CovenantError(
                "admit_staged requires a staging_dir; construct the gate with "
                "HermeticGate(..., staging_dir=<path the renderer cannot write>)"
            )
        self._staging_dir.mkdir(parents=True, exist_ok=True)

        tmp = self._staging_dir / f".incoming-{os.getpid()}-{len(self._digests)}"
        h = hashlib.sha256()
        with open(path, "rb") as src, open(tmp, "wb") as dst:
            while chunk := src.read(1 << 20):
                h.update(chunk)
                dst.write(chunk)
        digest = h.hexdigest()

        # <staging>/<digest>/<original name>. The digest directory makes staging
        # content-addressed (same bytes stage once, ever); keeping the original
        # filename inside it means anything downstream that takes a basename --
        # ComfyUI's logs, a node's display name -- still shows "model.safetensors"
        # rather than a 64-char hash.
        final_dir = self._staging_dir / digest
        final = final_dir / Path(path).name
        if final.exists():
            tmp.unlink(missing_ok=True)   # already staged by an earlier admit
        else:
            final_dir.mkdir(parents=True, exist_ok=True)
            os.replace(tmp, final)

        try:
            self._admit_digest(digest, path, role)
        except BaseException:
            raise
        return final, digest

    def admit_open(self, path: str | Path, role: str):
        """Open ONCE, then hash, resolve and record from that same handle.

        PARTIAL MITIGATION ONLY -- prefer `admit_staged`. This closes the window
        against path REPLACEMENT (os.replace, unlink+create: a new inode misses
        the memo and is re-hashed) but NOT against IN-PLACE OVERWRITE, because
        truncating and rewriting keeps the same inode and this handle follows it.

        `admit(path)` followed by a separate `open(path)` hashes one file and
        reads another if the bytes are swapped in between: the covenant would then
        attest the APPROVED digest while the renderer consumed different content.
        Because a covenant is signed, timestamped and offline-verifiable, that
        produces authenticated FALSE evidence -- strictly worse than issuing none.
        An adversary with write access to the asset store during a render is
        precisely who the hermetic-build model claims to defend against, so this
        is in-scope, not a theoretical.

        Holding one descriptor across hash-and-use removes the second lookup: the
        bytes hashed and the bytes read are the same open file, even if the path
        is replaced immediately afterwards.

        Returns (file_object, digest). The caller MUST read from the returned
        object, never re-open the path.
        """
        fh = open(path, "rb")
        try:
            digest = self._digest_fh(fh)
            self._admit_digest(digest, path, role)
        except BaseException:
            fh.close()
            raise
        return fh, digest

    def open_asset(self, path: str | Path, role: str, mode: str = "rb"):
        """admit() then open, returning only the file object.

        RETAINS A TOCTOU WINDOW between the hash and this open, and cannot avoid
        one, because it re-opens by name. Prefer `admit_open`. This exists for
        callers that need a plain file object and accept the residual risk; the
        window is far smaller than the adapter's (which must return a path), but
        it is not zero.
        """
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

    def set_staging_dir(self, staging_dir: str | Path) -> None:
        """Enable content-addressed staging after construction.

        Lets an adapter turn staging on for a gate the caller built, without the
        caller needing to know the adapter requires it.
        """
        self._staging_dir = Path(staging_dir)

    @property
    def staging_enabled(self) -> bool:
        """True when admit_staged is usable — i.e. a staging_dir was supplied."""
        return self._staging_dir is not None

    @property
    def hermetic(self) -> bool:
        return self._strict and not self._escapes

    @property
    def admitted_paths(self) -> set[str]:
        """Absolute paths that crossed the gate. Used by the escape probe."""
        return set(self._admitted_paths)

    @property
    def context(self) -> dict:
        return dict(self._context)

    @property
    def policy(self) -> Policy:
        return self._policy
