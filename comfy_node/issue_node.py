"""The node: write the master, then covenant exactly those bytes.

It writes the PNG itself rather than chaining off SaveImage, for the same reason
the library-mode driver does: a covenant binds a digest, so the bytes on disk
must be bytes this code chose. ComfyUI's SaveImage embeds the workflow as PNG
metadata, and anything that rewrites or strips that metadata later would break a
covenant taken over the annotated file.

The gate is NOT opened here. It was opened at prompt start (see session.py) and
closes at prompt end; this node reads the session that bracket created. Every way
that can be missing is a refusal, because a covenant issued without a gate would
assert a hermetic render nobody enforced.
"""

from __future__ import annotations

import json
from pathlib import Path

from smoke_covenant import CovenantError, issue

from . import completeness, session as session_mod

_CATEGORY = "Smoke/Covenant"


class SmokeCovenantIssue:
    """Bind this render's admitted ingredients to the master bytes and sign."""

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "images": ("IMAGE", {"tooltip": "The master(s) to covenant."}),
                "config_path": ("STRING", {
                    "default": "covenant.json",
                    "tooltip": "JSON config naming the asset store, policy and "
                               "render context. Read at PROMPT START, before any "
                               "node runs -- editing it mid-queue affects the "
                               "next prompt, not this one.",
                }),
                "filename_prefix": ("STRING", {"default": "covenant/master"}),
            },
            "hidden": {"prompt": "PROMPT", "unique_id": "UNIQUE_ID"},
        }

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("covenant_path",)
    FUNCTION = "issue_covenant"
    OUTPUT_NODE = True
    CATEGORY = _CATEGORY
    DESCRIPTION = (
        "Issues a Render Covenant over the incoming image: a signed, offline-"
        "verifiable statement binding the exact master bytes to the licensed "
        "assets the render actually loaded through the hermetic gate."
    )

    @classmethod
    def IS_CHANGED(cls, **kwargs):
        """Always re-run. A cached covenant node would silently skip issuing and
        leave the previous run's file looking current."""
        return float("nan")

    def issue_covenant(self, images, config_path, filename_prefix,
                       prompt=None, unique_id=None):
        sess = _require_session(config_path)
        gate, cfg = sess.gate, sess.config
        _refuse_on_gate_refusals(gate)
        _check_completeness(prompt, unique_id, gate)

        signer, ephemeral = _build_signer(cfg)
        out_dir, previewable = _output_dir(cfg)
        written, covenants, subfolder = _write_all(images, filename_prefix, out_dir,
                                                   gate, signer, cfg, sess)

        ui = {"text": [_summary(gate, sess, covenants, ephemeral)]}
        if previewable:
            # subfolder must be the one get_save_image_path chose, or /view cannot
            # resolve the thumbnail for any prefix containing a directory.
            ui["images"] = [{"filename": p.name, "subfolder": subfolder,
                             "type": "output"} for p in written]
        return {"ui": ui, "result": ("\n".join(str(p) for p in covenants),)}


# --- refusals ----------------------------------------------------------------


def _require_session(config_path: str):
    """The session the prompt-start bracket armed, or a refusal explaining why not."""
    provider = session_mod.provider()
    if provider is None:
        raise CovenantError(
            "no per-prompt gate seam is installed, so nothing bracketed this "
            f"render: {session_mod.SEAM_ERROR or 'the node package failed to register'}"
        )
    sess = provider.current_session()
    if sess is None:
        raise CovenantError(
            "no gate was opened for this prompt. The gate is armed at prompt "
            "start by reading the running graph; that did not happen here. Most "
            "likely this node was invoked outside ComfyUI's normal prompt "
            "execution, or the workflow was submitted before the node package "
            f"finished loading. (config_path={config_path!r})"
        )
    if sess.error:
        raise CovenantError(f"the gate could not be armed for this prompt: {sess.error}")
    if not sess.armed or sess.config is None:
        raise CovenantError("the gate for this prompt is not armed; refusing to issue")
    return sess


def _refuse_on_gate_refusals(gate) -> None:
    """Surface a denied asset as the node error, which is the whole product.

    `issue()` refuses on this too; catching it here means the operator reads
    which asset and which licence, not a generic assembly failure.
    """
    if not gate.refusals:
        return
    first = gate.refusals[0]
    raise CovenantError(
        f"BLOCKED by the Render Covenant gate: {Path(first.path).name}\n"
        f"  {first.reason}: {first.error}\n"
        f"  {len(gate.refusals)} refusal(s) in this render. No covenant is issued "
        "over a render that was denied an ingredient."
    )


def _check_completeness(prompt, unique_id, gate) -> None:
    """Refuse if an asset in this node's lineage never crossed the gate."""
    if not isinstance(prompt, dict) or unique_id is None:
        raise CovenantError(
            "ComfyUI did not supply the prompt graph (hidden PROMPT/UNIQUE_ID). "
            "Without it the cached-loader check cannot run, and an unchecked "
            "covenant could understate the render -- refusing to issue."
        )
    missing = completeness.missing_ingredients(
        prompt, str(unique_id), [i.label for i in gate.ingredients]
    )
    if missing:
        raise CovenantError(completeness.explain(missing))


# --- writing -----------------------------------------------------------------


def _output_dir(cfg) -> tuple[Path, bool]:
    """Where masters go, and whether ComfyUI's UI can preview them from there."""
    import folder_paths

    default = Path(folder_paths.get_output_directory())
    if cfg.output_dir is None:
        return default, True
    return cfg.output_dir, False


def _write_all(images, filename_prefix, out_dir, gate, signer, cfg, sess):
    """One master + one covenant per image in the batch.

    Returns (masters, covenants, subfolder) -- the subfolder is ComfyUI's, and
    the UI needs it to find the thumbnail.
    """
    import folder_paths
    import numpy as np
    from PIL import Image

    out_dir.mkdir(parents=True, exist_ok=True)
    height, width = int(images[0].shape[0]), int(images[0].shape[1])
    folder, base, counter, subfolder, _prefix = folder_paths.get_save_image_path(
        filename_prefix, str(out_dir), width, height
    )
    Path(folder).mkdir(parents=True, exist_ok=True)

    clients = _tsa_clients(cfg)
    written, covenants = [], []
    for offset, image in enumerate(images):
        array = (image.detach().cpu().numpy() * 255.0).clip(0, 255).astype(np.uint8)
        master = Path(folder) / f"{base}_{counter + offset:05}_.png"
        Image.fromarray(array).save(master)  # no metadata: we own these bytes
        covenant, _ingredients = issue(
            gate, str(master), signer=signer,
            renderer_identity=_renderer_identity(cfg, sess),
            tsa_clients=clients,
        )
        target = master.with_suffix(".covenant.json")
        target.write_text(json.dumps(covenant.to_dict(), indent=2), encoding="utf-8")
        _write_pubkey(master, signer)
        written.append(master)
        covenants.append(target)
    return written, covenants, subfolder


def _write_pubkey(master: Path, signer) -> None:
    """Ship the verifying key beside the covenant.

    It is a convenience, NOT a trust root: a key that travels with the artefact
    proves only self-consistency. A verifier must pin the signer out of band.
    """
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    master.with_suffix(".pubkey.pem").write_bytes(
        signer.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
    )


def _build_signer(cfg) -> tuple[object, bool]:
    """(signer, is_ephemeral). An ephemeral key still signs, but binds no identity.

    JUDGEMENT CALL, spelled out rather than papered over: the vendored
    `DemoSigner` (smoke_covenant._vendor.signer) generates a fresh EPHEMERAL
    in-memory key when constructed with none. That is fine for a demo; for a
    production node it means a covenant signed under a key nobody can pin --
    close to worthless as evidence. So an operator-supplied `signing_key_pem`
    is honoured exactly as it always was (loaded and wrapped, never regenerated),
    and DemoSigner's ephemeral-generation path fires ONLY when no key is
    configured at all -- and that fallback is called out LOUDLY in the node's
    UI text (see `_summary`), so nobody mistakes a demo-signed covenant for
    production evidence.

    The wrapper class for a CONFIGURED key prefers smoke_trust's
    SoftwareMeasurementSigner when the suite is present (unchanged behaviour
    for an in-tree install) and falls back to the vendored DemoSigner
    otherwise -- it satisfies the identical sign()/public_key() protocol over
    a supplied key, so the signature this produces is the same either way.
    Preferring smoke_trust here is a nicety, not a requirement: this node must
    work fully without it.
    """
    if cfg.signing_key_pem is not None:
        from cryptography.hazmat.primitives.serialization import load_pem_private_key

        key = load_pem_private_key(cfg.signing_key_pem.read_bytes(), password=None)
        return _keyed_signer_class()(key), False

    from smoke_covenant._vendor.signer import DemoSigner

    return DemoSigner(), True


def _keyed_signer_class():
    """The class that wraps an operator-supplied signing key. Prefers
    smoke_trust's SoftwareMeasurementSigner when importable (identical
    in-tree behaviour); otherwise the vendored DemoSigner, which exists
    precisely to satisfy this same protocol standalone -- see its docstring."""
    try:
        from smoke_trust.capsule.measurement import SoftwareMeasurementSigner

        return SoftwareMeasurementSigner
    except Exception:  # noqa: BLE001 - absence is the whole standalone point
        from smoke_covenant._vendor.signer import DemoSigner

        return DemoSigner


def _tsa_clients(cfg):
    if not cfg.anchor:
        return []
    from smoke_covenant._vendor.tsa import (
        DEFAULT_COMMERCIAL_TSA_URL, DEFAULT_SIGSTORE_TSA_URL, TSAClient,
    )

    return [TSAClient(DEFAULT_COMMERCIAL_TSA_URL), TSAClient(DEFAULT_SIGSTORE_TSA_URL)]


def _renderer_identity(cfg, sess) -> dict:
    try:
        from comfyui_version import __version__ as comfy_version
    except Exception:  # noqa: BLE001
        comfy_version = "unknown"
    return {
        "engine": "ComfyUI",
        "mode": "server",
        "version": comfy_version,
        "prompt_id": sess.prompt_id,
        "config": str(cfg.source),
        **dict(cfg.renderer_identity),
    }


def _summary(gate, sess, covenants, ephemeral: bool) -> str:
    lines = [f"{len(gate.ingredients)} ingredient(s), hermetic={gate.hermetic}"]
    lines += [f"  {i.role:12} {i.label[:44]:44} {i.grant_id}" for i in gate.ingredients]
    offthread = sess.offthread_resolutions
    if offthread:
        lines.append(
            "  note: " + ", ".join(f"{k}x{v}" for k, v in sorted(offthread.items()))
            + " resolved off the render thread and were NOT gated (usually the "
              "ComfyUI web UI browsing models; see README COVERAGE)"
        )
    if ephemeral:
        lines.append(
            "  WARNING: UNPINNED EPHEMERAL DEMO KEY -- this covenant cannot be "
            "verified by anyone else. No signing_key_pem was configured, so this "
            "run generated a fresh in-memory key that dies with the process. It "
            "verifies the bytes are self-consistent and proves nothing about who "
            "signed them. Do NOT treat this as production evidence -- set "
            "signing_key_pem in the config to sign with a key someone can pin."
        )
    lines += [f"  covenant: {p}" for p in covenants]
    return "\n".join(lines)
