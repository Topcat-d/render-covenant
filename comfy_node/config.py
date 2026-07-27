"""Build a gate's asset store, policy and render context from one JSON file.

WHY A FILE AND NOT NODE WIDGETS.
The gate has to be armed BEFORE the first node runs (see session.py), so its
inputs cannot come from node execution — nothing has executed yet. A path is the
only thing the seam can read early, so the path is the only thing on the node.

WHY RELATIVE PATHS RESOLVE AGAINST THE CONFIG FILE.
A rights config is a document a studio checks into its own repo, not a thing
whose meaning depends on ComfyUI's working directory.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from smoke_covenant import AssetStore, CovenantError, Grant, toy_territory_window_policy
from smoke_covenant.grants import Policy
from smoke_covenant.policies import LICENCE_TERMS, media_licence_policy

_POLICIES = {
    "media_licence": media_licence_policy,
    "toy_territory_window": toy_territory_window_policy,
}


class CovenantConfigError(CovenantError):
    """The config is unusable. Never fall back to a permissive default: an
    unreadable rights config must stop the render, not silently widen it."""


@dataclass(frozen=True)
class CovenantConfig:
    """Everything the session needs to open a gate and later issue a covenant."""

    source: Path
    store: AssetStore
    policy: Policy
    context: dict
    strict: bool = True
    record_only: bool = False
    audit_escapes: bool = False
    thread_affinity: bool = True
    staging_dir: Path | None = None
    output_dir: Path | None = None
    signing_key_pem: Path | None = None
    anchor: bool = False
    renderer_identity: dict = field(default_factory=dict)


def load_config(path: str | Path) -> CovenantConfig:
    """Parse and validate a covenant config. Raises CovenantConfigError."""
    src = Path(path).expanduser()
    if not src.is_file():
        raise CovenantConfigError(f"covenant config not found: {src}")
    try:
        raw = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CovenantConfigError(f"cannot read covenant config {src}: {exc}") from exc
    if not isinstance(raw, dict):
        raise CovenantConfigError(f"covenant config {src} must be a JSON object")

    base = src.parent
    policy_name = raw.get("policy", "media_licence")
    if policy_name not in _POLICIES:
        raise CovenantConfigError(
            f"unknown policy {policy_name!r}; known: {sorted(_POLICIES)}"
        )

    context = raw.get("context")
    if not isinstance(context, dict):
        raise CovenantConfigError("covenant config needs a 'context' object")

    return CovenantConfig(
        source=src,
        store=_build_store(raw.get("assets"), base, src),
        policy=_POLICIES[policy_name](),
        context=dict(context),
        strict=bool(raw.get("strict", True)),
        record_only=bool(raw.get("record_only", False)),
        audit_escapes=bool(raw.get("audit_escapes", False)),
        thread_affinity=bool(raw.get("thread_affinity", True)),
        staging_dir=_opt_path(raw.get("staging_dir"), base),
        output_dir=_opt_path(raw.get("output_dir"), base),
        signing_key_pem=_opt_path(raw.get("signing_key_pem"), base),
        anchor=bool(raw.get("anchor", False)),
        renderer_identity=dict(raw.get("renderer_identity") or {}),
    )


def _opt_path(value: Any, base: Path) -> Path | None:
    if value in (None, ""):
        return None
    p = Path(str(value)).expanduser()
    return p if p.is_absolute() else (base / p).resolve()


def _build_store(assets: Any, base: Path, src: Path) -> AssetStore:
    """Register every declared asset. An asset that will not register is fatal.

    Registering hashes the file, so a config naming a path that no longer exists
    fails here rather than mid-render — the operator learns at arming time.
    """
    if not isinstance(assets, list) or not assets:
        raise CovenantConfigError(
            f"covenant config {src} declares no assets; a gate with an empty store "
            "refuses every load, which is a misconfiguration rather than a policy"
        )
    store = AssetStore()
    for i, entry in enumerate(assets):
        if not isinstance(entry, dict):
            raise CovenantConfigError(f"assets[{i}] must be an object")
        raw_path = entry.get("path")
        if not raw_path:
            raise CovenantConfigError(f"assets[{i}] needs a 'path'")
        asset_path = _opt_path(raw_path, base)
        if asset_path is None or not asset_path.is_file():
            raise CovenantConfigError(f"assets[{i}] path does not exist: {asset_path}")
        store.register(
            asset_path,
            _build_grant(entry, i),
            label=str(entry.get("label") or asset_path.name),
        )
    return store


def _build_grant(entry: Mapping[str, Any], i: int) -> Grant:
    """One asset's grant. `licence` names a transcribed real licence; `terms`
    supplies them inline. Exactly one — silently preferring either would hide a
    typo in the other."""
    licence = entry.get("licence")
    terms = entry.get("terms")
    if (licence is None) == (terms is None):
        raise CovenantConfigError(
            f"assets[{i}] needs exactly one of 'licence' (a key of "
            f"{sorted(LICENCE_TERMS)}) or 'terms' (an inline object)"
        )
    if licence is not None:
        if licence not in LICENCE_TERMS:
            raise CovenantConfigError(
                f"assets[{i}] unknown licence {licence!r}; known: {sorted(LICENCE_TERMS)}"
            )
        terms = LICENCE_TERMS[licence]
    elif not isinstance(terms, dict):
        raise CovenantConfigError(f"assets[{i}] 'terms' must be an object")

    grant_id = entry.get("grant_id") or f"licence:{terms.get('licence', 'unnamed')}"
    return Grant(
        grant_id=str(grant_id),
        asset_digest="",  # bound by AssetStore.register from the real bytes
        kind=str(entry.get("kind", "model_licence")),
        terms=dict(terms),
        signer_spki=entry.get("signer_spki"),
        signature=entry.get("signature"),
    )
