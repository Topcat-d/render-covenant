"""Shared path resolution for the Render Covenant demo and tests.

Every script in this directory used to hardcode absolute paths from the
author's own machine -- a ComfyUI checkout and a virtualenv, both under a home
directory nobody else has -- which meant nothing here ran for anyone who cloned
the repo somewhere else. This module centralizes the two lookups that mattered:

  resolve_comfyui_root()   REQUIRED. Where the scripts that drive a real
                           ComfyUI (render_covenant_demo.py, and the
                           test_comfy_*, test_embedding_gap, test_large_asset
                           tests) find ComfyUI's `folder_paths` module.
                           There is no sane fallback if it is missing, so
                           this raises SystemExit with the exact list of
                           places it looked.

  resolve_suite_root()     OPTIONAL. Where `smoke_trust` (real signing keys,
                           RFC 3161 anchoring) and this repo's own
                           `sdks/agent/python` live. In the standalone public
                           repo there IS no suite root, so this returns None
                           rather than raising -- a script that does not
                           need `smoke_trust` must not be dragged down
                           because a sibling script does. Scripts that DO
                           need it call require_suite_root() to turn a miss
                           into a clear, actionable error instead of a bare
                           ModuleNotFoundError deep in an import.

Both search in a fixed order and stop at the first hit. Set the env vars
below to skip the search entirely and point at an exact location.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

COMFYUI_ROOT_ENV = "COMFYUI_ROOT"
SUITE_ROOT_ENV = "SMOKE_COVENANT_SUITE"  # same name comfy_node/__init__.py already uses


# --- ComfyUI ------------------------------------------------------------------


def _looks_like_comfyui(path: Path) -> bool:
    """`folder_paths.py` at the root is the module every script here actually
    imports, so its presence is a much better signal than "a directory named
    ComfyUI exists"."""
    return path.is_dir() and (path / "folder_paths.py").is_file()


def _comfyui_candidates() -> list[Path]:
    home = Path.home()
    return [
        HERE.parent / "ComfyUI",         # ../ComfyUI relative to covenant/
        HERE.parent.parent / "ComfyUI",  # ../../ComfyUI relative to covenant/
        home / "ComfyUI",
        home / "comfyui",
    ]


def find_comfyui_root() -> Path | None:
    """Non-raising probe: the exact same search as `resolve_comfyui_root()`
    (same order, same env var, same `_looks_like_comfyui` check), but
    returns None instead of raising when nothing is found.

    For callers that need to know WHETHER a ComfyUI checkout exists before
    deciding what to do about it -- e.g. a pytest-mode guard that wants to
    skip cleanly instead of letting `resolve_comfyui_root()`'s SystemExit
    escape module import. Scripts that just need ComfyUI or a clear fatal
    error should keep calling `resolve_comfyui_root()` / `bootstrap_comfy()`
    directly; this probe never raises, so it must not be used anywhere the
    fail-closed standalone behaviour is expected.
    """
    override = os.environ.get(COMFYUI_ROOT_ENV)
    if override:
        candidate = Path(override).expanduser()
        return candidate.resolve() if _looks_like_comfyui(candidate) else None

    for candidate in _comfyui_candidates():
        if _looks_like_comfyui(candidate):
            return candidate.resolve()
    return None


def resolve_comfyui_root() -> Path:
    """Find a real ComfyUI checkout.

    Order, stopping at the first hit:
      1. $COMFYUI_ROOT
      2. sibling locations relative to this repo: ../ComfyUI, ../../ComfyUI
      3. common user locations: ~/ComfyUI, ~/comfyui

    Raises SystemExit naming the env var and every directory checked if
    nothing matches -- importing the wrong `folder_paths` would silently
    change what the gate is being tested against, so there is no fallback
    worth guessing at.
    """
    root = find_comfyui_root()
    if root is not None:
        return root

    override = os.environ.get(COMFYUI_ROOT_ENV)
    if override:
        candidate = Path(override).expanduser()
        raise SystemExit(
            f"{COMFYUI_ROOT_ENV}={override!r} does not look like a ComfyUI checkout "
            f"(expected to find {candidate / 'folder_paths.py'}).\n"
            f"Fix the {COMFYUI_ROOT_ENV} environment variable, or unset it to let "
            "this script auto-detect a ComfyUI install."
        )

    candidates = _comfyui_candidates()
    looked = "\n".join(f"  - {c}" for c in candidates)
    raise SystemExit(
        "Could not find a ComfyUI checkout. Looked in:\n"
        f"{looked}\n\n"
        f"Set {COMFYUI_ROOT_ENV} to point at yours, e.g.\n"
        f'  {COMFYUI_ROOT_ENV}="/path/to/ComfyUI" python render_covenant_demo.py'
    )


# --- smoke-suite (smoke_trust, sdks/agent/python) ------------------------------


def _looks_like_suite(path: Path) -> bool:
    """Mirrors comfy_node/__init__.py's own `_suite_root()` marker check, so
    both places agree on what "found the suite" means."""
    return (path / "covenant" / "smoke_covenant" / "__init__.py").is_file() and (
        path / "trust" / "smoke_trust" / "__init__.py"
    ).is_file()


def resolve_suite_root() -> Path | None:
    """Find the smoke-suite checkout that carries `smoke_trust`, if any.

    Order, stopping at the first hit:
      1. $SMOKE_COVENANT_SUITE
      2. every parent directory of this file (the in-tree layout: covenant/
         is a subdirectory of the suite, at whatever depth it lives at)

    Returns None -- never raises -- when nothing is found. Only some scripts
    genuinely need `smoke_trust`; the rest must keep working.
    """
    override = os.environ.get(SUITE_ROOT_ENV)
    if override:
        candidate = Path(override).expanduser()
        return candidate.resolve() if _looks_like_suite(candidate) else None

    for candidate in (HERE, *HERE.parents):
        if _looks_like_suite(candidate):
            return candidate
    return None


def require_suite_root(reason: str) -> Path:
    """Like resolve_suite_root(), but turn a miss into a clear SystemExit
    instead of leaving the caller to hit a bare ModuleNotFoundError on
    `smoke_trust` later. Use this from scripts that cannot proceed at all
    without a suite root (they are not optional for these -- see the
    per-script docstrings for which ones)."""
    root = resolve_suite_root()
    if root is not None:
        return root

    override = os.environ.get(SUITE_ROOT_ENV)
    if override:
        detail = f"{SUITE_ROOT_ENV}={override!r} does not look like a smoke-suite checkout"
    else:
        detail = (
            "no smoke-suite checkout found above or at "
            f"{HERE} (looked for trust/smoke_trust/__init__.py alongside "
            "covenant/smoke_covenant/__init__.py in every parent directory)"
        )
    raise SystemExit(
        f"{reason}\n{detail}.\nSet {SUITE_ROOT_ENV}=/path/to/smoke-suite to point at one."
    )


# --- pytest / standalone dual-mode stops ---------------------------------------


def skip_or_die(reason: str, *, exit_code: int = 0) -> None:
    """Turn a "this test cannot run here" condition into the right kind of
    stop for whichever runner is in charge.

    Standalone (`python test_X.py`): this repo's tests are deliberately
    fail-closed on a missing dependency (see the module docstring) -- raises
    SystemExit(exit_code), exactly as every test script already did before
    pytest support existed. Callers are expected to have already printed
    their own existing-style `[SKIP]` banner (see test_vendor_conformance.py
    / test_comfy_node.py) before calling this, so standalone output is
    unchanged byte-for-byte; this does not print anything of its own, to
    avoid tacking a redundant line onto that banner.

    Under pytest (`"pytest" in sys.modules`): a module-level SystemExit is a
    BaseException that escapes pytest's collector and crashes the whole run
    with an INTERNALERROR instead of being reported as a normal result. So
    under pytest this calls `pytest.skip(reason, allow_module_level=True)`
    instead, which pytest reports as an honest skip -- `reason` is what
    shows up in pytest's output, so make it a short, self-contained sentence
    even when the caller's own banner is more elaborate.

    Not a fit for `resolve_comfyui_root()`'s own SystemExit, which already
    carries a more detailed, established message of its own -- callers
    should let that one raise on its own and only use `skip_or_die` to
    short-circuit into a pytest skip *before* calling it (see the
    ComfyUI-dependent test files).
    """
    if "pytest" in sys.modules:
        import pytest

        pytest.skip(reason, allow_module_level=True)
    raise SystemExit(exit_code)


# --- sys.path wiring ------------------------------------------------------------


def add_syspath(*paths: Path | None) -> None:
    for p in paths:
        if p is None:
            continue
        s = str(p)
        if s not in sys.path:
            sys.path.insert(0, s)


def bootstrap_comfy(*, need_suite: bool = False) -> tuple[Path, Path | None]:
    """For scripts that need a real ComfyUI on sys.path.

    Resolves ComfyUI (required) and the suite root (optional unless
    `need_suite=True`), pushes ComfyUI, this directory, and -- if found --
    the suite's `trust` and `sdks/agent/python` onto sys.path, and returns
    (comfy_root, suite_root_or_None).
    """
    comfy = resolve_comfyui_root()
    if need_suite:
        suite: Path | None = require_suite_root(
            "this script needs smoke_trust (real signing / RFC 3161 anchoring)."
        )
    else:
        suite = resolve_suite_root()

    add_syspath(comfy, HERE)
    if suite is not None:
        add_syspath(suite / "trust", suite / "sdks" / "agent" / "python")
    return comfy, suite


def script_prefix() -> str:
    """The path prefix to put in front of an example command like
    `demo_covenant.py`, so the suggested command is correct in BOTH layouts:

      - the in-tree smoke-suite monorepo, where `covenant/` is a subdirectory
        and the natural place to run commands from is the suite root above
        it -- prefix "covenant/";
      - the standalone public repo, where this directory itself IS the repo
        root and every script is a direct sibling of wherever the command is
        run from -- prefix "" (no prefix at all).

    Reuses the exact same signal `resolve_suite_root()` already computes (a
    suite root found above this file means the in-tree layout), so this can
    never disagree with what `bootstrap_suite()` / `bootstrap_comfy()`
    actually do.
    """
    return "covenant/" if resolve_suite_root() is not None else ""


def bootstrap_suite(*, need_suite: bool = True) -> Path | None:
    """For scripts that need `smoke_trust` but not a real ComfyUI.

    Pushes this directory onto sys.path always, and -- if found -- the
    suite's `trust` and `sdks/agent/python`. When `need_suite=True` (the
    default) a missing suite root raises a clear SystemExit; pass
    `need_suite=False` for a caller that wants to degrade gracefully (e.g.
    skip a check) instead of failing.
    """
    if need_suite:
        suite: Path | None = require_suite_root(
            "this script needs smoke_trust (real signing / RFC 3161 anchoring)."
        )
    else:
        suite = resolve_suite_root()

    add_syspath(HERE)
    if suite is not None:
        add_syspath(suite / "trust", suite / "sdks" / "agent" / "python")
    return suite
