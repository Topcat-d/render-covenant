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

INSTALL: symlink or junction this directory into ComfyUI/custom_nodes/, e.g.
  mklink /J C:\\Users\\me\\ComfyUI\\custom_nodes\\smoke_render_covenant ^
            C:\\path\\to\\smoke-suite\\covenant\\comfy_node
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

_LOG = logging.getLogger(__name__)


def _suite_root() -> Path | None:
    """Locate smoke-suite so `smoke_covenant` and `smoke_trust` are importable.

    Searched in order: the SMOKE_COVENANT_SUITE override, then every parent of
    this file -- which resolves both the in-tree layout and a symlinked install,
    because Path.resolve() follows the link back into the repo.
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


def _bootstrap_path() -> Path:
    root = _suite_root()
    if root is None:
        raise ImportError(
            "smoke-suite not found from "
            f"{Path(__file__).resolve()}. Install this node by symlinking "
            "smoke-suite/covenant/comfy_node into ComfyUI/custom_nodes, or set "
            "SMOKE_COVENANT_SUITE to the smoke-suite checkout."
        )
    for sub in ("covenant", "trust", "sdks/agent/python"):
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

_LOG.info("Render Covenant: %s", install())

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "SUITE_ROOT"]
