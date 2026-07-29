"""Exercise the ComfyUI CUSTOM NODE package against real ComfyUI modules.

The library-mode adapter test (test_comfy_adapter.py) proves the gate patches
folder_paths correctly. This proves the SERVER-MODE packaging around it: that the
node registers the way ComfyUI expects, that the per-prompt bracket opens and --
the part that matters -- closes without leaving a patch behind, that the gate is
confined to the render thread, and that a loader served from ComfyUI's cache is
caught instead of quietly producing a short ingredient list.

Nothing here needs a GPU or a real checkpoint: stand-in files exercise every
decision the packaging makes.

ComfyUI is auto-detected (see _paths.py); set COMFYUI_ROOT to override.

Run:
  "$COMFYUI_ROOT/.venv/Scripts/python.exe" covenant/test_comfy_node.py
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
import threading
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import HERE, SUITE_ROOT_ENV, bootstrap_comfy  # noqa: E402

# NOTE: the NODE no longer needs smoke_trust -- it falls back to the vendored
# DemoSigner/TSAClient and loads standalone (proven separately). This TEST still
# requires the suite root, deliberately: it exercises the in-tree path including
# the "second copy adopts the existing provider" case, which resolves
# smoke_covenant via SMOKE_COVENANT_SUITE. Requiring it here is a property of the
# test fixture, not of the node.
COMFY, SUITE = bootstrap_comfy(need_suite=True)

import folder_paths  # noqa: E402  (real ComfyUI)

PASS, FAIL = "  [PASS]", "  [FAIL]"
failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"{PASS if ok else FAIL} {name}" + (f"\n         {detail}" if detail else ""))
    if not ok:
        failures.append(name)


def banner(title: str) -> None:
    print(f"\n{'-' * 72}\n{title}\n{'-' * 72}")


# --- fixtures ----------------------------------------------------------------


def make_assets(tmp: Path) -> tuple[Path, Path, Path]:
    """A granted LoRA, an ungranted one, and a config naming only the first."""
    lora_dir = Path(folder_paths.get_folder_paths("loras")[0])
    lora_dir.mkdir(parents=True, exist_ok=True)
    granted = lora_dir / "covnode_granted.safetensors"
    ungranted = lora_dir / "covnode_ungranted.safetensors"
    granted.write_bytes(b"<stand-in for a licensed LoRA>")
    ungranted.write_bytes(b"<stand-in for a scraped LoRA>")
    folder_paths.cache_helper.clear()

    config = tmp / "covenant.json"
    config.write_text(json.dumps({
        "policy": "toy_territory_window",
        "output_dir": str(tmp / "out"),
        "context": {"territory": "US", "channels": ["paid-social"],
                    "release_end": "2027-02-01"},
        "assets": [{
            "path": str(granted),
            "grant_id": "model_grant_204",
            "kind": "model_grant",
            "signer_spki": "demo-brand",
            "terms": {"territories": ["US"], "expires_on": "2028-01-01"},
        }],
    }, indent=2), encoding="utf-8")
    return granted, ungranted, config


def graph_for(config: Path, lora_name: str, *, with_loader: bool = True) -> dict:
    """An API-format prompt: a loader feeding the covenant node."""
    graph = {
        "9": {"class_type": "SmokeCovenantIssue", "inputs": {
            "images": ["1", 0],
            "config_path": str(config),
            "filename_prefix": "covtest/master",
        }},
    }
    if with_loader:
        graph["1"] = {"class_type": "LoraLoaderModelOnly",
                      "inputs": {"lora_name": lora_name}}
    return graph


# --- checks ------------------------------------------------------------------


def test_import_and_mappings(pkg) -> None:
    banner("1. ComfyUI custom-node convention (nodes.py:2276-2282)")
    ccm = getattr(pkg, "NODE_CLASS_MAPPINGS", None)
    ndn = getattr(pkg, "NODE_DISPLAY_NAME_MAPPINGS", None)
    check("exports NODE_CLASS_MAPPINGS as a dict", isinstance(ccm, dict))
    check("exports NODE_DISPLAY_NAME_MAPPINGS as a dict", isinstance(ndn, dict))
    check("display names cover exactly the mapped node ids",
          isinstance(ccm, dict) and isinstance(ndn, dict) and set(ccm) == set(ndn),
          f"{sorted(ccm or {})} vs {sorted(ndn or {})}")
    node_id = pkg.session.NODE_ID
    check("mapping key equals session.NODE_ID (the config lookup depends on it)",
          node_id in (ccm or {}), f"NODE_ID={node_id!r}, keys={sorted(ccm or {})}")

    cls = (ccm or {}).get(node_id)
    check("node class declares INPUT_TYPES / RETURN_TYPES / FUNCTION / CATEGORY",
          all(hasattr(cls, a) for a in
              ("INPUT_TYPES", "RETURN_TYPES", "FUNCTION", "CATEGORY")))
    check("FUNCTION names a real method on the class",
          cls is not None and callable(getattr(cls, cls.FUNCTION, None)))
    spec = cls.INPUT_TYPES()
    check("required inputs are images + config_path + filename_prefix",
          set(spec.get("required", {})) == {"images", "config_path", "filename_prefix"},
          str(sorted(spec.get("required", {}))))
    check("requests hidden PROMPT and UNIQUE_ID (needed for the cache check)",
          spec.get("hidden", {}) == {"prompt": "PROMPT", "unique_id": "UNIQUE_ID"},
          str(spec.get("hidden")))
    check("IS_CHANGED forces re-execution", cls.IS_CHANGED() != cls.IS_CHANGED())


def test_seam_registered(pkg) -> None:
    banner("2. per-prompt seam: registered as a lifecycle-only CacheProvider")
    from comfy_execution.cache_provider import _get_cache_providers

    provider = pkg.session.provider()
    check("provider was created at import", provider is not None,
          pkg.session.SEAM_ERROR or "")
    check("provider is registered with ComfyUI", provider in _get_cache_providers())
    check("declines to participate in caching", provider.should_cache(None) is False)
    check("on_lookup never returns a cache hit",
          asyncio.run(provider.on_lookup(None)) is None)
    check("on_store is a no-op", asyncio.run(provider.on_store(None, None)) is None)


def test_lifecycle(pkg, config: Path, granted: Path) -> None:
    banner("3. the bracket: opens at prompt start, closes at prompt end")
    import comfy.sd1_clip as sd1

    provider = pkg.session.provider()
    graph = graph_for(config, granted.name)
    provider._resolve_graph = lambda pid: graph

    before = (folder_paths.get_full_path, folder_paths.get_annotated_filepath,
              sd1.load_embed)
    provider.on_prompt_start("p-lifecycle")
    sess = provider.current_session()
    check("a session is armed for the running prompt",
          sess is not None and sess.armed, sess.error if sess else "no session")
    check("get_full_path is patched while the prompt runs",
          folder_paths.get_full_path is not before[0])
    check("load_embed is patched while the prompt runs", sd1.load_embed is not before[2])

    resolved = folder_paths.get_full_path("loras", granted.name)
    check("granted asset resolves through the gate", resolved is not None)
    check("the load was recorded as an ingredient",
          sess is not None and len(sess.gate.ingredients) == 1,
          f"{len(sess.gate.ingredients) if sess else '-'} ingredient(s)")

    provider.on_prompt_end("p-lifecycle")
    check("get_full_path restored by name",
          folder_paths.get_full_path.__name__ == "get_full_path",
          f"still {folder_paths.get_full_path.__name__}")
    check("get_annotated_filepath restored by name",
          folder_paths.get_annotated_filepath.__name__ == "get_annotated_filepath",
          f"still {folder_paths.get_annotated_filepath.__name__}")
    check("load_embed restored by name", sd1.load_embed.__name__ == "load_embed",
          f"still {sd1.load_embed.__name__}")
    check("all three restored to the identical original objects",
          (folder_paths.get_full_path, folder_paths.get_annotated_filepath,
           sd1.load_embed) == before)
    check("the session is gone after prompt end",
          provider.current_session() is None)


def test_not_armed_and_bad_config(pkg, tmp: Path, granted: Path) -> None:
    banner("4. prompts we must NOT touch, and configs we must refuse")
    provider = pkg.session.provider()
    pristine = folder_paths.get_full_path

    provider._resolve_graph = lambda pid: {"1": {"class_type": "KSampler", "inputs": {}}}
    provider.on_prompt_start("p-nocov")
    check("a prompt with no covenant node arms nothing",
          provider.current_session() is None)
    check("...and patches nothing", folder_paths.get_full_path is pristine)
    provider.on_prompt_end("p-nocov")

    missing = tmp / "does-not-exist.json"
    provider._resolve_graph = lambda pid: graph_for(missing, granted.name)
    provider.on_prompt_start("p-badcfg")
    sess = provider.current_session()
    check("an unreadable config records a session ERROR (not a silent skip)",
          sess is not None and sess.error is not None,
          (sess.error if sess else "no session at all"))
    leaked = folder_paths.get_full_path is not pristine
    check("a failed arming leaves no patch behind", not leaked,
          f"leaked {folder_paths.get_full_path}" if leaked else "")
    provider.on_prompt_end("p-badcfg")


def test_refusal(pkg, config: Path, granted: Path, ungranted: Path) -> None:
    banner("5. an ungranted asset is refused mid-prompt")
    from smoke_covenant.adapters.comfy import ComfyGateRefusal

    provider = pkg.session.provider()
    provider._resolve_graph = lambda pid: graph_for(config, granted.name)
    provider.on_prompt_start("p-refuse")
    sess = provider.current_session()
    refused = False
    try:
        folder_paths.get_full_path("loras", ungranted.name)
    except ComfyGateRefusal:
        refused = True
    check("ungranted LoRA is REFUSED inside a server-mode prompt", refused)
    check("the refusal is kept for the operator",
          sess is not None and len(sess.gate.refusals) == 1)
    provider.on_prompt_end("p-refuse")


def test_thread_affinity(pkg, config: Path, granted: Path, ungranted: Path) -> None:
    banner("6. thread affinity: ComfyUI's web thread cannot poison a render")
    provider = pkg.session.provider()
    provider._resolve_graph = lambda pid: graph_for(config, granted.name)
    provider.on_prompt_start("p-thread")
    sess = provider.current_session()

    result: dict = {}

    def other_thread():
        # This is what /view_metadata does from aiohttp while a render runs.
        try:
            result["path"] = folder_paths.get_full_path("loras", ungranted.name)
        except BaseException as exc:  # noqa: BLE001
            result["error"] = f"{type(exc).__name__}: {exc}"

    worker = threading.Thread(target=other_thread)
    worker.start()
    worker.join()

    check("an off-thread resolution does not raise into the web server",
          "error" not in result, result.get("error", ""))
    check("an off-thread resolution resolves normally",
          result.get("path") is not None)
    poisoned = sess is None or len(sess.gate.refusals) != 0
    check("an off-thread resolution does NOT poison the render's refusals",
          not poisoned,
          f"{len(sess.gate.refusals) if sess else '-'} refusal(s) leaked in"
          if poisoned else "")
    check("off-thread resolutions are counted and reportable",
          sess is not None and sum(sess.offthread_resolutions.values()) >= 1,
          str(sess.offthread_resolutions if sess else {}))
    provider.on_prompt_end("p-thread")


def test_completeness_unit(pkg, config: Path, granted: Path) -> None:
    banner("7. completeness check: ancestors only, cached loader detected")
    comp = pkg.completeness
    graph = graph_for(config, granted.name)
    graph["77"] = {"class_type": "LoraLoaderModelOnly",
                   "inputs": {"lora_name": "covnode_ungranted.safetensors"}}

    anc = comp.ancestors(graph, "9")
    check("ancestor walk reaches the loader that fed the image", "1" in anc)
    check("ancestor walk excludes a disconnected node", "77" not in anc, str(sorted(anc)))

    required = comp.required_assets(graph, "9")
    check("the lineage's LoRA is required",
          granted.name.lower() in required, str(sorted(required)))
    check("the disconnected node's LoRA is not required",
          "covnode_ungranted.safetensors" not in required)

    missing = comp.missing_ingredients(graph, "9", [])
    check("a cached loader (nothing admitted) is DETECTED", granted.name.lower() in missing)
    check("nothing is missing once the asset is admitted",
          comp.missing_ingredients(graph, "9", [granted.name]) == {})


def test_node_end_to_end(pkg, config: Path, granted: Path, tmp: Path) -> None:
    banner("8. the node itself: refuses without a gate, issues with one")
    import torch
    from smoke_covenant import CovenantError, Covenant, verify
    from cryptography.hazmat.primitives.serialization import load_pem_public_key

    node = pkg.NODE_CLASS_MAPPINGS[pkg.session.NODE_ID]()
    images = torch.rand(1, 8, 8, 3)
    graph = graph_for(config, granted.name)
    provider = pkg.session.provider()
    provider._resolve_graph = lambda pid: graph

    try:
        node.issue_covenant(images, str(config), "covtest/master", graph, "9")
        check("issuing with no armed gate is refused", False, "it issued anyway")
    except CovenantError as exc:
        check("issuing with no armed gate is refused", True, str(exc).splitlines()[0])

    # A prompt whose loader was served from ComfyUI's cache: no gate resolution.
    provider.on_prompt_start("p-cached")
    try:
        node.issue_covenant(images, str(config), "covtest/master", graph, "9")
        check("a cached loader blocks issuance", False, "it issued a short list")
    except CovenantError as exc:
        check("a cached loader blocks issuance", "never crossed the gate" in str(exc),
              str(exc).splitlines()[0])
    provider.on_prompt_end("p-cached")

    # The honest path: the loader really ran.
    provider.on_prompt_start("p-good")
    folder_paths.get_full_path("loras", granted.name)
    out = node.issue_covenant(images, str(config), "covtest/master", graph, "9")
    provider.on_prompt_end("p-good")

    cov_path = Path(out["result"][0])
    check("a covenant file was written", cov_path.is_file(), str(cov_path))
    # <name>.png -> <name>.covenant.json, so strip the doubled suffix back off.
    stem = str(cov_path).removesuffix(".covenant.json")
    master, pubkey = Path(stem + ".png"), Path(stem + ".pubkey.pem")
    check("the master PNG sits beside it", master.is_file(), str(master))
    check("the verifying key sits beside it", pubkey.is_file(), str(pubkey))

    data = json.loads(cov_path.read_text(encoding="utf-8"))
    covenant = Covenant(body=data["body"], signature_r=data["signature"]["r"],
                        signature_s=data["signature"]["s"],
                        signer_spki=data["signature"]["signer_spki"],
                        anchor=data["anchor"])
    pub = load_pem_public_key(pubkey.read_bytes())
    verify(covenant, str(master), pub, require_anchor=False)
    check("the covenant verifies offline against the written master", True)
    check("it records the one ingredient the render loaded",
          covenant.body["ingredients"]["count"] == 1,
          str(covenant.body["ingredients"]))
    check("it is marked hermetic", covenant.body["decision"]["hermetic"] is True)
    check("renderer identity says ComfyUI server mode",
          covenant.body["renderer"].get("mode") == "server"
          and covenant.body["renderer"].get("engine") == "ComfyUI",
          str(covenant.body["renderer"]))

    # A batch must produce one covenant per image, each bound to its own bytes.
    provider.on_prompt_start("p-batch")
    folder_paths.get_full_path("loras", granted.name)
    batch = node.issue_covenant(torch.rand(2, 8, 8, 3), str(config),
                                "covtest/batch", graph, "9")
    provider.on_prompt_end("p-batch")
    batch_paths = batch["result"][0].splitlines()
    check("a 2-image batch writes 2 covenants", len(batch_paths) == 2, str(batch_paths))
    check("each batch covenant binds different master bytes",
          len({json.loads(Path(p).read_text())["body"]["master"]["digest"]
               for p in batch_paths}) == 2)

    tampered = master.with_name("tampered.png")
    blob = bytearray(master.read_bytes())
    blob[-1] ^= 0x01
    tampered.write_bytes(bytes(blob))
    try:
        verify(covenant, str(tampered), pub, require_anchor=False)
        check("a one-byte change breaks verification", False, "it verified")
    except CovenantError:
        check("a one-byte change breaks verification", True)


def test_second_copy(pkg, tmp: Path) -> None:
    """A copied (not symlinked) install, loaded the way nodes.py loads it."""
    banner("9. a second installed copy adopts the seam instead of doubling it")
    import importlib.util
    from comfy_execution.cache_provider import _get_cache_providers

    dest = tmp / "custom_nodes" / "smoke_render_covenant_copy"
    shutil.copytree(HERE / "comfy_node", dest,
                    ignore=shutil.ignore_patterns("__pycache__"))
    os.environ[SUITE_ROOT_ENV] = str(SUITE)
    try:
        name = "smoke_render_covenant_copy"
        spec = importlib.util.spec_from_file_location(name, dest / "__init__.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001
        check("a copied install imports via SMOKE_COVENANT_SUITE", False,
              f"{type(exc).__name__}: {exc}")
        return
    finally:
        os.environ.pop(SUITE_ROOT_ENV, None)

    check("a copied install imports via SMOKE_COVENANT_SUITE", True,
          f"suite root {module.SUITE_ROOT}")
    check("it exports the same node id",
          set(module.NODE_CLASS_MAPPINGS) == set(pkg.NODE_CLASS_MAPPINGS))
    registered = [p for p in _get_cache_providers()
                  if type(p).__name__ == "CovenantSessionProvider"]
    check("exactly one provider is registered process-wide", len(registered) == 1,
          f"{len(registered)} registered -- a second would double-patch folder_paths")
    check("the second copy adopted the first copy's provider",
          module.session.provider() is pkg.session.provider())


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="covenant-node-test-"))
    granted, ungranted, config = make_assets(tmp)
    print("=" * 72)
    print(f"Render Covenant custom node vs real ComfyUI  ({COMFY})")
    print("=" * 72)
    try:
        import comfy_node as pkg  # the package under test, imported as ComfyUI would
    except Exception as exc:  # noqa: BLE001
        check("comfy_node imports", False, f"{type(exc).__name__}: {exc}")
        return 1
    check("comfy_node imports", True, f"suite root {pkg.SUITE_ROOT}")

    try:
        test_import_and_mappings(pkg)
        test_seam_registered(pkg)
        test_lifecycle(pkg, config, granted)
        test_not_armed_and_bad_config(pkg, tmp, granted)
        test_refusal(pkg, config, granted, ungranted)
        test_thread_affinity(pkg, config, granted, ungranted)
        test_completeness_unit(pkg, config, granted)
        test_node_end_to_end(pkg, config, granted, tmp)
        test_second_copy(pkg, tmp)
    finally:
        for f in (granted, ungranted):
            f.unlink(missing_ok=True)
        folder_paths.cache_helper.clear()
        shutil.rmtree(tmp, ignore_errors=True)

    print("=" * 72)
    if failures:
        print(f"FAILED: {len(failures)} -> {', '.join(failures)}")
        return 1
    print("All custom-node checks passed against real ComfyUI.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
