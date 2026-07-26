"""ComfyUI adapter: route every model and image resolution through the gate.

WHY A RUNTIME HOOK AND NOT A SOURCE PATCH.
ComfyUI is GPL-3.0. Patching its tree would make this package derivative and
collide with releasing it permissively. A runtime monkeypatch is the established
custom-node pattern, is conventionally a separate work, and survives ComfyUI
updates that a source patch would not. The licence and the engineering point the
same way.

THE CHOKE POINTS, read from ComfyUI @806e092 `folder_paths.py`:
  get_full_path(folder_name, filename) -> str | None        (line 422)
      checkpoints, loras, vae, controlnet, clip, unet, embeddings...
      `folder_name` IS the ingredient role, for free.
  get_full_path_or_raise(folder_name, filename) -> str      (line 442)
      calls get_full_path internally, so patching the inner one covers both.
      Patch ONLY the inner one or every admitted asset is recorded twice.
  get_annotated_filepath(name, default_dir) -> str          (line 324)
      LoadImage and friends.

A FOURTH CHOKE POINT, and it does not live in folder_paths:
  comfy.sd1_clip.load_embed(name, embedding_directory, size, key)   (sd1_clip.py:415)
      Textual-inversion embeddings. nodes.py:629 hands the checkpoint loader a
      DIRECTORY LIST via get_folder_paths, so embeddings never touch
      get_full_path and used to bypass the gate entirely. See `_EmbedHook`.

COVERAGE, stated honestly (premise 4 — coverage is part of the theorem):
These four cover models, UI-supplied images and embeddings. They do NOT cover a
custom node that opens an arbitrary path with builtins.open, downloads a URL at
runtime, or reads from a database. Such a node escapes the gate, and an escape
INVALIDATES the hermetic claim rather than weakening it. `audit_escapes=True`
installs a builtins.open probe to surface them; it is a diagnostic, not a
guarantee.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from ..gate import HermeticGate
from ..grants import AssetNotRegistered, CovenantError, PolicyDenied, digest_file

# folder_paths folder names that carry rights weight. Anything else resolved
# through get_full_path is still admitted, using the folder name as its role.
_ROLE_ALIASES = {
    "checkpoints": "checkpoint",
    "loras": "lora",
    "vae": "vae",
    "controlnet": "controlnet",
    "clip": "text_encoder",
    "clip_vision": "clip_vision",
    "unet": "unet",
    "diffusion_models": "unet",
    "embeddings": "embedding",
    "style_models": "style_model",
    "upscale_models": "upscale_model",
}


class ComfyGateRefusal(CovenantError):
    """Raised inside ComfyUI when an asset fails to resolve to an acceptable grant.

    Surfaces as a node error in the UI. That is the intended behaviour: a gate
    that does not stop the render is a log, and logs are what this design exists
    to replace.
    """


class _EmbedHook:
    """Routes textual-inversion embeddings through the gate.

    comfy.sd1_clip.load_embed resolves the file INSIDE itself (sd1_clip.py:421-440)
    and returns a tensor, so wrapping it does not hand us the path. Mirroring its
    resolution is the only way to learn what it opened -- and a private mirror can
    drift when upstream changes.

    So drift fails CLOSED rather than silently reopening the hole: if the mirror
    resolves nothing but the real load_embed returns an embedding, an embedding
    was loaded that the gate never saw, and that is reported as an escape. A
    stale mirror therefore degrades to a loud refusal, never to a quiet bypass.
    """

    def __init__(self, gate: HermeticGate, admit) -> None:
        self._gate = gate
        self._admit = admit
        self._orig = None
        self._mod = None

    def install(self) -> None:
        import comfy.sd1_clip as sd1

        self._mod = sd1
        self._orig = sd1.load_embed
        orig, admit, gate = self._orig, self._admit, self._gate

        def resolve(name, directory):
            """Mirror of sd1_clip.py:415-440. Kept deliberately literal."""
            if isinstance(directory, str):
                directory = [directory]
            if directory is None:
                return None
            try:
                directory = sd1.expand_directory_list(directory)
            except Exception:
                return None
            for d in directory:
                p = os.path.abspath(os.path.join(d, name))
                d = os.path.abspath(d)
                try:
                    if os.path.commonpath((d, p)) != d:
                        continue
                except Exception:
                    continue
                if os.path.isfile(p):
                    return p
                for ext in (".safetensors", ".pt", ".bin"):
                    if os.path.isfile(p + ext):
                        return p + ext
            return None

        def patched(embedding_name, embedding_directory, embedding_size, embed_key=None):
            path = resolve(embedding_name, embedding_directory)
            if path is not None:
                admit(path, "embedding")
            result = orig(embedding_name, embedding_directory, embedding_size, embed_key)
            if result is not None and path is None:
                # Upstream found a file our mirror did not. Fail closed.
                gate.note_escape(
                    f"embedding {embedding_name!r} loaded by comfy.sd1_clip.load_embed "
                    "but not resolvable by the covenant mirror -- the mirror has "
                    "drifted from sd1_clip.py and coverage cannot be claimed"
                )
            return result

        sd1.load_embed = patched

    def uninstall(self) -> None:
        if self._mod is not None and self._orig is not None:
            self._mod.load_embed = self._orig


@contextmanager
def covenant_gate(
    gate: HermeticGate,
    *,
    record_only: bool = False,
    audit_escapes: bool = False,
) -> Iterator[HermeticGate]:
    """Route ComfyUI's asset resolution through `gate` for the duration.

    record_only=True admits everything and records what WOULD have been refused,
    for incremental adoption. It marks the render non-hermetic, so a covenant
    issued from it carries `hermetic: false` and a verifier can see the
    difference. Default is strict, because a gate that does not gate is a log.
    """
    import folder_paths  # imported here so the package never hard-depends on ComfyUI

    orig_full = folder_paths.get_full_path
    orig_annotated = folder_paths.get_annotated_filepath
    would_refuse: list[tuple[str, str]] = []

    def _admit(path: str, role: str) -> None:
        # No hashing here on purpose. The gate memoizes internally, so an adapter
        # that pre-hashed would double the cold cost and save nothing warm --
        # which is exactly the bug test_large_asset.py caught.
        try:
            gate.admit(path, role)
        except (AssetNotRegistered, PolicyDenied) as exc:
            if record_only:
                would_refuse.append((path, str(exc)))
                return
            raise ComfyGateRefusal(
                f"blocked by Render Covenant gate: {Path(path).name} ({role})\n  {exc}"
            ) from exc

    def patched_get_full_path(folder_name: str, filename: str):
        result = orig_full(folder_name, filename)
        if result is None:
            return None  # not found: nothing was read, nothing to admit
        _admit(result, _ROLE_ALIASES.get(folder_name, folder_name))
        return result

    def patched_get_annotated_filepath(name: str, default_dir=None):
        result = orig_annotated(name, default_dir)
        if result and os.path.isfile(result):
            _admit(result, "input_image")
        return result

    folder_paths.get_full_path = patched_get_full_path
    folder_paths.get_annotated_filepath = patched_get_annotated_filepath

    embed_hook = _EmbedHook(gate, _admit)
    embed_hook.install()

    escape_probe = _install_escape_probe(gate, folder_paths) if audit_escapes else None
    try:
        yield gate
    finally:
        folder_paths.get_full_path = orig_full
        folder_paths.get_annotated_filepath = orig_annotated
        embed_hook.uninstall()
        if escape_probe is not None:
            escape_probe()
        if record_only and would_refuse:
            gate.note_escape(f"record_only: {len(would_refuse)} asset(s) would have been refused")


def _install_escape_probe(gate: HermeticGate, folder_paths) -> "callable":
    """Diagnostic: flag reads of model-shaped files that bypassed the gate.

    Deliberately narrow — it watches for known weight extensions under the model
    roots, not every open() in the process, so it does not fire on ComfyUI's own
    config and cache traffic. It finds honest mistakes; it does not stop a node
    that is determined to read around the gate. Returns an uninstall callable.
    """
    import builtins

    watched = {".safetensors", ".ckpt", ".pt", ".pth", ".gguf", ".bin"}
    roots = set()
    for entry in folder_paths.folder_names_and_paths.values():
        for p in entry[0]:
            roots.add(os.path.abspath(p))

    orig_open = builtins.open
    seen: set[str] = set()

    def probing_open(file, mode="r", *a, **kw):
        try:
            if isinstance(file, (str, os.PathLike)) and "r" in str(mode):
                p = os.path.abspath(os.fspath(file))
                if Path(p).suffix.lower() in watched and any(
                    p.startswith(r) for r in roots
                ):
                    digest = None
                    try:
                        digest = digest_file(p)
                    except OSError:
                        pass
                    if digest and not any(
                        i.asset_digest == digest for i in gate.ingredients
                    ):
                        if p not in seen:
                            seen.add(p)
                            gate.note_escape(p)
        except CovenantError:
            raise
        except Exception:  # a diagnostic must never break the render
            pass
        return orig_open(file, mode, *a, **kw)

    builtins.open = probing_open

    def uninstall() -> None:
        builtins.open = orig_open

    return uninstall
