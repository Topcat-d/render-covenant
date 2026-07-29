"""Render Covenants as a ComfyUI custom node.

LICENSING, WHICH SHAPED THE WHOLE DESIGN.
ComfyUI is GPL-3.0. This package does not vendor, patch, fork or redistribute any
part of it. It is a separate work that ComfyUI loads and that hooks ComfyUI's
public extension points at runtime -- the established custom-node pattern, and
conventionally a separate work rather than a derivative one. Every ComfyUI import
in this package is deferred to call time or to load time inside ComfyUI's own
process; nothing here contains ComfyUI code. The licence and the engineering
point the same way: a runtime hook also survives ComfyUI updates that a source
patch would not.

WHAT IT DOES.
At prompt start it opens a `HermeticGate` over the asset store and policy named
by a JSON config, so every model, LoRA, VAE, embedding and input image the render
resolves is checked against a grant before its bytes are read. At the end of the
graph the `SmokeCovenantIssue` node writes the master PNG and binds those exact
bytes to the ingredients the render actually loaded, signed and offline-verifiable.

READ README.md's COVERAGE SECTION BEFORE RELYING ON THIS. The gate covers what
routes through `folder_paths` and `comfy.sd1_clip.load_embed`, on the render
thread. Reads outside that escape it, and an escape invalidates the hermetic
claim rather than weakening it.

INSTALL: symlink or junction this directory (with `smoke_covenant` alongside
it -- see `_covenant_dir()` below) into ComfyUI/custom_nodes/. A smoke-suite
checkout above it is an OPTIONAL bonus, not a requirement -- see README.md's
Install section for the standalone case and what it changes about signing.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

_LOG = logging.getLogger(__name__)


def _covenant_dir() -> Path:
    """Where `smoke_covenant` (and its `_vendor/` fallbacks) live.

    Not a search: this file's own parent directory always has `smoke_covenant`
    as a direct sibling, in EVERY layout this package ships in --

      - in-tree monorepo:  smoke-suite/covenant/{comfy_node,smoke_covenant}
      - standalone copy:   wherever-you-put-it/{comfy_node,smoke_covenant}

    -- because the two are packaged and copied together. So this is just
    `Path(__file__).resolve().parent.parent`, true by construction, with no
    dependency on anything living above it.
    """
    return Path(__file__).resolve().parent.parent


def _suite_root() -> Path | None:
    """Locate an OPTIONAL smoke-suite checkout, for `smoke_trust` (a preferred
    but non-required signer/anchor path -- see issue_node._build_signer) and
    `sdks/agent/python`.

    Searched in order: the SMOKE_COVENANT_SUITE override, then every parent of
    this file -- which resolves both the in-tree layout and a symlinked install,
    because Path.resolve() follows the link back into the repo. Returns None,
    never raises, when nothing is found: a standalone install has no suite root
    and no `smoke_trust`, and that is a normal, fully-supported configuration,
    not an error.
    """
    candidates: list[Path] = []
    override = os.environ.get("SMOKE_COVENANT_SUITE")
    if override:
        candidates.append(Path(override).expanduser())
    candidates.extend(Path(__file__).resolve().parents)
    for root in candidates:
        if ((root / "covenant" / "smoke_covenant" / "__init__.py").is_file()
                and (root / "trust" / "smoke_trust" / "__init__.py").is_file()):
            return root
    return None


def _find_covenant_dir(suite_root: Path | None) -> Path | None:
    """Where to add to sys.path so `import smoke_covenant` succeeds.

    Tried in order:
      1. This file's own sibling directory (`_covenant_dir()`) -- true by
         construction whenever comfy_node was copied or symlinked as a WHOLE
         alongside smoke_covenant, which is the normal case both in-tree and
         standalone.
      2. `suite_root / "covenant"`, if a smoke-suite checkout was found --
         covers a comfy_node directory installed on its own (e.g. only
         comfy_node symlinked/copied into ComfyUI/custom_nodes, with
         SMOKE_COVENANT_SUITE or an in-tree parent pointing at the rest).

    Returns None only if neither locates it -- the one case where failing to
    load this node is correct.
    """
    direct = _covenant_dir()
    if (direct / "smoke_covenant" / "__init__.py").is_file():
        return direct
    if suite_root is not None:
        via_suite = suite_root / "covenant"
        if (via_suite / "smoke_covenant" / "__init__.py").is_file():
            return via_suite
    return None


def _bootstrap_path() -> Path | None:
    """Wire sys.path so `smoke_covenant` (REQUIRED) is importable, and
    `smoke_trust` / `sdks/agent/python` (OPTIONAL, bonus) too if a smoke-suite
    checkout happens to be found above this install. Returns the suite root if
    one was found, else None -- absence must NOT stop this node from loading;
    only a genuinely missing `smoke_covenant` is fatal.
    """
    root = _suite_root()
    covenant_dir = _find_covenant_dir(root)
    if covenant_dir is None:
        raise ImportError(
            f"smoke_covenant not found beside this file ({_covenant_dir()}), "
            "and no smoke-suite checkout was found to provide it either. "
            "comfy_node normally ships as a sibling of smoke_covenant -- if "
            "this directory was copied out on its own, copy smoke_covenant "
            "(including its _vendor/ subpackage) alongside it, or set "
            "SMOKE_COVENANT_SUITE to a smoke-suite checkout that has one."
        )
    if str(covenant_dir) not in sys.path:
        sys.path.insert(0, str(covenant_dir))

    if root is not None:
        for sub in ("trust", "sdks/agent/python"):
            path = root / sub
            if path.is_dir() and str(path) not in sys.path:
                sys.path.insert(0, str(path))
    return root


SUITE_ROOT = _bootstrap_path()

from .issue_node import SmokeCovenantIssue  # noqa: E402  (needs the path above)
from .session import NODE_ID, install  # noqa: E402

# ComfyUI's V1 convention, read from nodes.py:2276-2282: a custom-node module
# exports NODE_CLASS_MAPPINGS (node id -> class) and optionally
# NODE_DISPLAY_NAME_MAPPINGS (node id -> label). The id is the `class_type` that
# appears in a saved workflow, so it is API surface and must stay stable --
# session.NODE_ID depends on it to find its config before any node runs.
NODE_CLASS_MAPPINGS = {NODE_ID: SmokeCovenantIssue}
NODE_DISPLAY_NAME_MAPPINGS = {NODE_ID: "Render Covenant (Issue)"}

_LOG.info(
    "Render Covenant: %s (smoke-suite: %s)",
    install(),
    f"found at {SUITE_ROOT}" if SUITE_ROOT is not None
    else "not found -- standalone mode, signing falls back to the vendored "
         "DemoSigner when no signing_key_pem is configured",
)

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "SUITE_ROOT"]
