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
            name, directory = embedding_name, embedding_directory
            if path is not None:
                admitted = admit(path, "embedding")
                if admitted and admitted != path:
                    # Staging is on. Point load_embed at the STAGED copy, or it
                    # re-resolves the original directory and loads bytes the gate
                    # no longer controls -- the same TOCTOU the model path had.
                    staged = os.path.abspath(admitted)
                    name, directory = os.path.basename(staged), [os.path.dirname(staged)]
            result = orig(name, directory, embedding_size, embed_key)
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
    audit_escapes: bool = True,
    staging_dir: str | Path | None = None,
) -> Iterator[HermeticGate]:
    """Route ComfyUI's asset resolution through `gate` for the duration.

    record_only=True admits everything and records what WOULD have been refused,
    for incremental adoption. It marks the render non-hermetic, so a covenant
    issued from it carries `hermetic: false` and a verifier can see the
    difference. Default is strict, because a gate that does not gate is a log.
    """
    import folder_paths  # imported here so the package never hard-depends on ComfyUI

    if staging_dir is not None:
        gate.set_staging_dir(staging_dir)

    orig_full = folder_paths.get_full_path
    orig_annotated = folder_paths.get_annotated_filepath
    would_refuse: list[tuple[str, str]] = []

    def _admit(path: str, role: str) -> str:
        """Admit `path` and return the path the renderer should actually open.

        With staging enabled this is the STAGED copy, and that return value is
        the whole point: get_full_path must hand ComfyUI a path, so handing back
        the original leaves the TOCTOU window wide open -- the gate hashes one
        file and the loader opens another, and a swap in between is attested as
        the approved asset. Returning the staged path means the bytes ComfyUI
        loads are the bytes that were admitted.

        No hashing here on purpose. The gate memoizes internally, so an adapter
        that pre-hashed would double the cold cost and save nothing warm --
        which is exactly the bug test_large_asset.py caught.
        """
        try:
            if gate.staging_enabled:
                staged, _ = gate.admit_staged(path, role)
                return str(staged)
            gate.admit(path, role)
            return path
        except (AssetNotRegistered, PolicyDenied) as exc:
            if record_only:
                would_refuse.append((path, str(exc)))
                return path
            raise ComfyGateRefusal(
                f"blocked by Render Covenant gate: {Path(path).name} ({role})\n  {exc}"
            ) from exc

    def patched_get_full_path(folder_name: str, filename: str):
        result = orig_full(folder_name, filename)
        if result is None:
            return None  # not found: nothing was read, nothing to admit
        # RETURN the admitted path, not the original: with staging on these differ,
        # and returning the original would hand ComfyUI a file the gate no longer
        # controls -- reopening the TOCTOU this whole path exists to close.
        return _admit(result, _ROLE_ALIASES.get(folder_name, folder_name))

    def patched_get_annotated_filepath(name: str, default_dir=None):
        result = orig_annotated(name, default_dir)
        if result and os.path.isfile(result):
            return _admit(result, "input_image")
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
    """Diagnostic: flag asset traffic that bypassed the gate.

    Watches two escape routes:
      - reads of weight-shaped files under the model roots that no ingredient
        accounts for (a node calling open() on a path directly)
      - runtime NETWORK FETCHES (urllib), because a node that downloads a model
        mid-render introduces an ingredient the gate never saw and never could

    DEFAULT ON as of the coverage-hardening pass. It was opt-in, which meant that
    in practice it never ran and `strict=True` promised a completeness it was not
    checking. A diagnostic nobody enables is not a control.

    Deliberately narrow on the file side: known weight extensions under the model
    roots, not every open() in the process, so it does not fire on ComfyUI's own
    config and cache traffic.

    WHAT THIS IS NOT. It finds honest mistakes and casual escapes. It does not
    stop a node determined to read around the gate — that node can use os.open,
    a C extension, mmap, or a socket directly. Genuinely closing this needs
    OS-level containment (a read-only bind mount, a sandbox, a container with the
    asset store mounted read-only), which is a deployment property this library
    cannot provide for itself. Returns an uninstall callable.
    """
    import builtins

    watched = {".safetensors", ".ckpt", ".pt", ".pth", ".gguf", ".bin"}
    roots = set()
    for entry in folder_paths.folder_names_and_paths.values():
        for p in entry[0]:
            roots.add(os.path.abspath(p))

    orig_open = builtins.open
    opened: set[str] = set()

    def probing_open(file, mode="r", *a, **kw):
        # COLLECT ONLY -- no hashing, no gate calls. Doing either here recursed
        # (digest_file calls open, which is this function) and flagged the gate's
        # own admit-time read as an escape, because the ingredient is not recorded
        # until after that read completes. Adjudication happens at teardown, when
        # the admitted set is final.
        try:
            if isinstance(file, (str, os.PathLike)) and "r" in str(mode):
                p = os.path.abspath(os.fspath(file))
                if Path(p).suffix.lower() in watched and any(
                    p.startswith(r) for r in roots
                ):
                    opened.add(p)
        except Exception:  # a diagnostic must never break the render
            pass
        return orig_open(file, mode, *a, **kw)

    builtins.open = probing_open

    # Network escape: a node that downloads a model mid-render introduces an
    # ingredient the gate never saw and structurally could not have admitted.
    # Flagged rather than blocked -- blocking urlopen would break legitimate
    # traffic (telemetry, model-index lookups) and this layer cannot tell them
    # apart. In strict mode note_escape raises, so a flagged fetch still stops
    # the render rather than quietly producing a covenant that overclaims.
    import urllib.request

    orig_urlopen = urllib.request.urlopen

    def probing_urlopen(url, *a, **kw):
        try:
            target = url if isinstance(url, str) else getattr(url, "full_url", "")
            gate.note_escape(f"network fetch during render: {str(target)[:200]}")
        except CovenantError:
            raise
        except Exception:  # a diagnostic must never break the render
            pass
        return orig_urlopen(url, *a, **kw)

    urllib.request.urlopen = probing_urlopen

    def uninstall() -> None:
        builtins.open = orig_open
        urllib.request.urlopen = orig_urlopen
        # Adjudicate now that the admitted set is complete. In strict mode
        # note_escape raises, so a genuine escape still refuses the covenant --
        # it just does so with a full picture instead of a racing one.
        # Subtract REFUSED paths as well as admitted ones. The gate hashes an
        # asset before it can refuse it, so a refusal necessarily leaves an open()
        # behind -- counting that as an escape would report the gate working as
        # the gate failing.
        accounted = gate.admitted_paths | {
            os.path.abspath(d.path) for d in gate.refusals
        }
        for p in sorted(opened - accounted):
            gate.note_escape(p)

    return uninstall
