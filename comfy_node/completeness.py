"""Catch the ingredient ComfyUI's output cache hid from the gate.

THE PROBLEM THIS EXISTS FOR, and it only exists in server mode.
ComfyUI caches node outputs ACROSS prompts (CLASSIC, LRU and the default
RAM_PRESSURE all do; only `--cache-none` does not). Re-queue a workflow and
`CheckpointLoaderSimple` is served from that cache: it does not run, so it never
calls `folder_paths.get_full_path`, so the gate never sees the checkpoint -- for
a render that unmistakably contains it.

Left alone, the covenant would then be signed, timestamped, offline-verifiable
and WRONG, listing zero or too few ingredients for a render that used more. The
gate's own docstring names that failure mode: authenticated false evidence is
worse than no evidence. A gap that produces it has to be detected, not noted.

THE CHECK.
Walk the static prompt graph backwards from the issuing node to its ancestors --
exactly the subgraph that produced these pixels -- and collect every widget value
that names a file ComfyUI can resolve (a model in any `folder_paths` folder, or
an annotated input file). Every one of those MUST appear among the assets the
gate admitted. One missing means something was read without crossing the gate,
and the covenant is refused.

WHAT IT DOES NOT CATCH, since a detector's coverage is part of the claim too:
  - a node that opens a path it built itself: no widget names it, so it is
    invisible here exactly as it is invisible to the gate;
  - nodes created by runtime subgraph expansion: they are not in the static
    prompt, so their assets are neither required nor checked (they are still
    gated if they resolve through folder_paths -- this check only stops being a
    guarantee, it does not open a hole);
  - assets referenced by a value that is not a plain string.

It can also over-require: a prompt string that happens to equal a model filename
would be demanded as an ingredient. That direction is a spurious REFUSAL, which
is the safe way for a completeness check to be wrong.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

_MAX_ANNOTATED_PROBE = 260  # a value longer than a Windows path is prose, not a filename


def ancestors(graph: dict, node_id: str) -> set[str]:
    """`node_id` and every node reachable backwards through its input links.

    ComfyUI encodes a link as `[source_node_id, output_index]`; anything else in
    an input slot is a literal widget value.
    """
    seen: set[str] = set()
    stack = [str(node_id)]
    while stack:
        current = stack.pop()
        if current in seen or not isinstance(graph.get(current), dict):
            continue
        seen.add(current)
        for value in (graph[current].get("inputs") or {}).values():
            if _is_link(value) and str(value[0]) in graph:
                stack.append(str(value[0]))
    return seen


def _is_link(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 2
        and isinstance(value[0], (str, int))
        and isinstance(value[1], int)
    )


def filename_index() -> dict[str, str]:
    """basename (lowercased) -> the folder_paths folder that offers it.

    Lowercased because the gate records what Windows handed back and a workflow
    records what the user picked, and those differ in case often enough to matter.
    """
    import folder_paths

    index: dict[str, str] = {}
    for folder in list(folder_paths.folder_names_and_paths.keys()):
        try:
            names = folder_paths.get_filename_list(folder)
        except Exception:  # noqa: BLE001 - a broken folder must not break the check
            continue
        for name in names:
            index.setdefault(Path(name).name.lower(), folder)
    return index


def required_assets(graph: dict, node_id: str) -> dict[str, str]:
    """basename (lowercased) -> role, for every asset this node's lineage names."""
    import folder_paths

    index = filename_index()
    required: dict[str, str] = {}
    for nid in ancestors(graph, node_id):
        for value in (graph[nid].get("inputs") or {}).values():
            if not isinstance(value, str) or not value.strip():
                continue
            key = Path(value).name.lower()
            if key in index:
                required.setdefault(key, index[key])
            elif _is_annotated_input(folder_paths, value):
                required.setdefault(key, "input_image")
    return required


def _is_annotated_input(folder_paths, value: str) -> bool:
    """Does this widget value name a file in ComfyUI's input directory?"""
    if len(value) > _MAX_ANNOTATED_PROBE or "\n" in value:
        return False
    try:
        return bool(folder_paths.exists_annotated_filepath(value))
    except Exception:  # noqa: BLE001 - a filesystem probe must not break the check
        return False


def missing_ingredients(graph: dict, node_id: str, admitted_labels: Iterable[str]) -> dict[str, str]:
    """Assets this render's lineage names that the gate never admitted."""
    have = {Path(str(label)).name.lower() for label in admitted_labels}
    return {name: role for name, role in required_assets(graph, node_id).items()
            if name not in have}


def explain(missing: dict[str, str]) -> str:
    """The operator-facing refusal. Names the likely cause and the actual fix."""
    listing = ", ".join(f"{name} ({role})" for name, role in sorted(missing.items()))
    return (
        f"refusing to issue: {len(missing)} asset(s) named by this render's lineage "
        f"never crossed the gate -- {listing}.\n"
        "  The usual cause is ComfyUI's cross-prompt output cache: a loader node "
        "served from cache does not run, so it never resolves its file and the gate "
        "cannot see it. The covenant would then understate the render.\n"
        "  Fixes: start ComfyUI with --cache-none, or change an input on the "
        "loader so it re-executes. Do not disable this check -- it is the only "
        "thing standing between a cached loader and a signed false ingredient list."
    )
