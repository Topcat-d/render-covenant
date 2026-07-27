"""Rights-addressable assets: the content-addressed store and grant resolution.

THE ONE INVENTION THIS PACKAGE EXISTS FOR:

    An asset cannot participate in a render unless its fingerprint resolves to
    an acceptable, authority-signed grant.

Everything else here is plumbing around that rule. Note what is deliberately
NOT here: a rights vocabulary. Territory, medium, sublicensing, expiration and
likeness semantics are the expensive layer, and they are expensive because
acceptance cannot be built, only won. The policy is a PLUGGABLE PREDICATE and
the bundled one is a labelled toy. Do not grow it here.

Non-goals (a design without non-goals is unfalsifiable):
  - does not prove an undisclosed asset was not copied in out of band
  - does not prove a generator's training set was clean
  - does not prove a grant signer told the truth, or held authority to sign
  - does not recognise a cropped, re-encoded or perceptually similar copy
  - does not prove anything about bytes that never crossed the gate
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping, Protocol

from ._vendor.primitives import canonical_json_bytes

_HASH_CHUNK = 1 << 20  # 1 MiB streaming reads


class CovenantError(Exception):
    """Base for every covenant failure. All of them fail closed."""


class AssetNotRegistered(CovenantError):
    """A render tried to read bytes that resolve to no registered asset."""


class NoGrant(CovenantError):
    """The asset is registered but carries no grant."""


class PolicyDenied(CovenantError):
    """A grant exists and the policy refused it for this render context."""


def digest_file(path: str | Path) -> str:
    """Streaming SHA-256 of a file. This IS an asset's identity."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(_HASH_CHUNK):
            h.update(chunk)
    return h.hexdigest()


def digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class Grant:
    """An authority's signed statement about one asset digest.

    `signature` and `signer_spki` are carried but NOT verified here — grant
    signature verification and authority-of-signer are separate concerns and
    authority is explicitly out of scope for v0 (see module non-goals). A grant
    with `signature=None` is a DEV grant and `Policy` implementations are
    expected to refuse it outside dev.
    """

    grant_id: str
    asset_digest: str
    kind: str  # "consent" | "sync_license" | "asset_license" | "model_grant" | ...
    terms: Mapping[str, object]  # opaque to this layer, read only by the policy
    signer_spki: str | None = None
    signature: str | None = None

    def leaf_record(self, role: str) -> dict:
        """The canonical ingredient record that becomes a Merkle leaf."""
        return {
            "asset_digest": self.asset_digest,
            "grant_id": self.grant_id,
            "grant_kind": self.kind,
            "role": role,
            "signer_spki": self.signer_spki,
        }


class Policy(Protocol):
    """Decides whether a grant admits an asset into THIS render.

    Implementations must be pure and deterministic: the covenant commits to
    `policy_id` + `policy_hash`, and a verifier re-running the decision must
    reach the same result.
    """

    policy_id: str

    def policy_hash(self) -> str:
        ...

    def evaluate(self, grant: Grant, context: Mapping[str, object]) -> None:
        """Return None to admit. RAISE PolicyDenied to refuse. Never return a bool —
        fail-closed means the refusal path cannot be accidentally ignored."""
        ...


@dataclass(frozen=True)
class PredicatePolicy:
    """Wraps a plain predicate as a Policy.

    The point of this class is that the interesting layer is replaceable. Bring
    your own rights engine; the gate does not care what decides, only that
    something did and that the decision is committed to.
    """

    policy_id: str
    predicate: Callable[[Grant, Mapping[str, object]], bool]
    source: str  # a stable description; hashed into the covenant

    def policy_hash(self) -> str:
        return digest_bytes(canonical_json_bytes({"id": self.policy_id, "src": self.source}))

    def evaluate(self, grant: Grant, context: Mapping[str, object]) -> None:
        if not self.predicate(grant, context):
            raise PolicyDenied(
                f"policy {self.policy_id} refused grant {grant.grant_id} "
                f"({grant.kind}) for asset {grant.asset_digest[:16]}..."
            )


def toy_territory_window_policy() -> PredicatePolicy:
    """A DELIBERATELY TOY policy. It checks two fields and nothing else.

    It exists so the demo runs end to end, and it is labelled loudly so nobody
    mistakes it for rights engineering. Real deployments replace this. It is NOT
    a clearance system and must never be described as one.
    """

    def predicate(grant: Grant, ctx: Mapping[str, object]) -> bool:
        terms = grant.terms
        territories = terms.get("territories")
        if territories is not None and ctx.get("territory") not in territories:
            return False
        expires = terms.get("expires_on")  # ISO date string, lexicographically comparable
        release = ctx.get("release_end")
        if expires is not None and release is not None and str(release) > str(expires):
            return False
        channels = terms.get("channels")
        if channels is not None:
            requested = ctx.get("channels") or []
            if not set(requested).issubset(set(channels)):
                return False
        return True

    return PredicatePolicy(
        policy_id="TOY-territory-window-v0",
        predicate=predicate,
        source="territories/expires_on/channels subset check ONLY — a demo stub, not rights engineering",
    )


class AssetStore:
    """Content-addressed store mapping asset digest -> grant.

    Deliberately dumb. A real deployment points this at a DAM or a rights system;
    the gate only requires `resolve(digest) -> Grant`.
    """

    def __init__(self) -> None:
        self._grants: dict[str, Grant] = {}
        self._labels: dict[str, str] = {}

    def register(self, path: str | Path, grant: Grant, label: str | None = None) -> str:
        """Register a file's bytes under a grant. Returns the asset digest."""
        actual = digest_file(path)
        if grant.asset_digest and grant.asset_digest != actual:
            raise CovenantError(
                f"grant {grant.grant_id} names digest {grant.asset_digest[:16]}... "
                f"but {path} hashes to {actual[:16]}... -- refusing to register"
            )
        bound = grant if grant.asset_digest else Grant(**{**grant.__dict__, "asset_digest": actual})
        self._grants[actual] = bound
        self._labels[actual] = label or str(Path(path).name)
        return actual

    def resolve(self, digest: str) -> Grant:
        """Digest -> Grant, or raise. There is no permissive path."""
        grant = self._grants.get(digest)
        if grant is None:
            raise AssetNotRegistered(
                f"no registered asset for digest {digest[:16]}... -- "
                "an unregistered asset cannot participate in a render"
            )
        return grant

    def label(self, digest: str) -> str:
        return self._labels.get(digest, digest[:16])

    def __contains__(self, digest: object) -> bool:
        return digest in self._grants
