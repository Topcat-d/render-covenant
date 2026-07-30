"""Preflight check for the Render Covenant demo suite.

ZERO-DEPENDENCY ON PURPOSE: stdlib only, no smoke_covenant import required to
run this file itself, so it works BEFORE the environment is set up -- that is
the entire point of a preflight check. Run it first, before anything else:

    python doctor.py

Checks, each printed as PASS/FAIL with an exact remedy command when it fails:
  1. Python version
  2. cryptography importable            (smoke_covenant's only hard dependency)
  3. smoke_covenant importable from this checkout
  4. ComfyUI root locatable              (COMFYUI_ROOT env var, or auto-detect)
  5. ComfyUI's venv python + torch (+ cryptography) importable there
  6. checkpoints present in models/checkpoints
  7. LoRAs present, specifically the two the three demo cases need
  8. network reachability of the two RFC 3161 timestamp authorities
  9. free disk space for a checkpoint download

Ends with a VERDICT block: exactly which demos are runnable right now, and
the single most useful next command.

Exit code: 0 if at least demo_covenant.py can run (the floor -- it needs no
ComfyUI, no models, no GPU); 1 if even that is blocked.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):  # Windows consoles default to cp1252
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

MIN_PYTHON = (3, 11)  # matches trust/pyproject.toml requires-python = ">=3.11"
CHECKPOINT_EXTS = {".safetensors", ".ckpt", ".pt", ".pth", ".gguf", ".bin"}
DMD2_HINT = "dmd2"
PIXEL_ART_HINT = "pixel-art"
DIGICERT_TSA = "http://timestamp.digicert.com"
SIGSTORE_TSA = "https://timestamp.sigstore.dev/api/v1/timestamp"
# SDXL checkpoints run ~6.5 GiB (see render_covenant_demo.py); leave headroom.
REQUIRED_FREE_GIB_FOR_CHECKPOINT = 8

TAG_PASS, TAG_FAIL = "[PASS]", "[FAIL]"

results: list[dict] = []


def check(name: str, ok: bool, detail: str = "", remedy: str = "") -> bool:
    tag = TAG_PASS if ok else TAG_FAIL
    line = f"{tag} {name}"
    if detail:
        line += f"\n       {detail}"
    if not ok and remedy:
        line += f"\n       Remedy: {remedy}"
    print(line)
    results.append({"name": name, "ok": ok, "detail": detail, "remedy": remedy})
    return ok


def banner(title: str) -> None:
    print(f"\n{'=' * 74}\n{title}\n{'=' * 74}")


# --- 1. Python version --------------------------------------------------------

def check_python_version() -> bool:
    v = sys.version_info
    ok = (v.major, v.minor) >= MIN_PYTHON
    detail = f"{v.major}.{v.minor}.{v.micro}  ({sys.executable})"
    remedy = (f"install Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ from "
              "https://python.org/downloads and re-run doctor.py with it")
    return check(f"Python version >= {MIN_PYTHON[0]}.{MIN_PYTHON[1]}", ok, detail, remedy)


# --- 2. cryptography -----------------------------------------------------------

def check_cryptography() -> bool:
    try:
        import cryptography
        ver = getattr(cryptography, "__version__", "unknown")
        return check("cryptography importable (the only hard dependency)", True,
                      f"version {ver}")
    except ModuleNotFoundError as exc:
        remedy = f'"{sys.executable}" -m pip install cryptography'
        return check("cryptography importable (the only hard dependency)", False,
                      str(exc), remedy)


# --- 3. smoke_covenant ---------------------------------------------------------

def check_smoke_covenant() -> bool:
    try:
        import smoke_covenant  # noqa: F401
        return check("smoke_covenant importable from this checkout", True,
                      str(HERE / "smoke_covenant"))
    except ModuleNotFoundError as exc:
        if exc.name == "cryptography":
            detail = "blocked by missing cryptography -- see check #2 above"
            remedy = f'"{sys.executable}" -m pip install cryptography'
        else:
            detail = str(exc)
            remedy = (f"run doctor.py from inside covenant/, or check that "
                      f"{exc.name!r} is importable on this interpreter")
        return check("smoke_covenant importable from this checkout", False, detail, remedy)
    except Exception as exc:  # noqa: BLE001 -- a preflight check must never crash
        return check("smoke_covenant importable from this checkout", False,
                      f"unexpected {type(exc).__name__}: {exc} -- this looks like a real "
                      "bug, not a missing dependency",
                      "re-run with -X dev or file an issue with the traceback")


# --- 4. ComfyUI root ------------------------------------------------------------

def _looks_like_comfyui(path: Path) -> bool:
    return (path / "folder_paths.py").is_file() and (path / "nodes.py").is_file()


def _paths_resolver():
    """Import the `_paths.py` resolver another lane of this hardening pass is
    building, if it exists yet. Never raises: absence, a partial module, or a
    resolver that itself errors are all treated the same as "not available"."""
    try:
        import _paths  # type: ignore  # may not exist yet -- built by another agent
    except Exception:
        return None
    for attr in ("resolve_comfyui_root", "get_comfyui_root", "find_comfyui_root", "comfyui_root"):
        fn = getattr(_paths, attr, None)
        if callable(fn):
            return attr, fn
    const = getattr(_paths, "COMFYUI_ROOT", None)
    if const:
        return "COMFYUI_ROOT", (lambda: const)
    return None


def _script_prefix() -> str:
    """"covenant/" when this doctor.py sits inside the smoke-suite monorepo
    (a suite root -- trust/smoke_trust alongside covenant/smoke_covenant -- is
    found above this file, via `_paths.resolve_suite_root()`), "" when this
    file's own directory IS the repo root, which is the standalone public
    repo's layout and where demo_covenant.py etc. are direct siblings of
    wherever the suggested command is run from.

    Reuses `_paths.script_prefix()` when `_paths` is importable (see
    `_paths_resolver()` above for why that import is defensive rather than
    assumed); falls back to the old hardcoded "covenant/" -- never worse than
    the behaviour this replaces -- only if that check itself cannot run.
    """
    try:
        import _paths  # type: ignore
        return _paths.script_prefix()
    except Exception:
        return "covenant/"


def _fallback_candidates() -> list[Path]:
    """Our own lookup, used only when _paths.py is absent (or gives nothing)
    and COMFYUI_ROOT is unset. Deliberately generic: a ComfyUI checkout that
    sits next to this suite, or in the user's home directory, is a common
    layout; nothing here is specific to one machine."""
    seen: list[Path] = []
    for c in (HERE.parents[1] / "ComfyUI", Path.home() / "ComfyUI"):
        if c not in seen:
            seen.append(c)
    return seen


def resolve_comfyui_root() -> tuple[Path | None, str]:
    """Returns (path_or_None, human-readable source of that answer).

    COMFYUI_ROOT, if set, is always honoured AS GIVEN and never silently
    swapped for a fallback -- an explicit env var that points nowhere useful
    should be reported clearly, not quietly second-guessed.
    """
    env = os.environ.get("COMFYUI_ROOT")
    if env:
        return Path(env), "COMFYUI_ROOT env var"

    resolver = _paths_resolver()
    if resolver is not None:
        attr, fn = resolver
        try:
            result = fn()
            if result:
                return Path(result), f"_paths.{attr}()"
        except SystemExit:
            # _paths.py's own resolver raises SystemExit (not Exception) when it
            # cannot find anything -- deliberately, per its own docstring, since
            # there is "no sane fallback" for IT to guess at. A bare `except
            # Exception` would miss that (SystemExit is a BaseException) and take
            # this entire preflight check down with it. Here it just means "that
            # resolver came up empty" -- fall through to our own lookup instead.
            pass
        except Exception:
            pass  # a resolver under construction may not work yet -- fall through

    for candidate in _fallback_candidates():
        if _looks_like_comfyui(candidate):
            return candidate, "auto-detected"

    return None, "not found"


def check_comfyui_root() -> Path | None:
    root, source = resolve_comfyui_root()
    if root is None:
        check("ComfyUI root locatable", False,
              "no ComfyUI checkout found (checked COMFYUI_ROOT and common locations)",
              "set COMFYUI_ROOT=/path/to/ComfyUI, or install ComfyUI: "
              "https://github.com/comfyanonymous/ComfyUI")
        return None
    if not root.is_dir():
        check("ComfyUI root locatable", False, f"{source} -> {root}  (directory does not exist)",
              f"set COMFYUI_ROOT to a real ComfyUI checkout (currently points at {root}, "
              "which does not exist)")
        return None
    if not _looks_like_comfyui(root):
        check("ComfyUI root locatable", False,
              f"{source} -> {root}  (exists, but no folder_paths.py/nodes.py -- "
              "doesn't look like a ComfyUI checkout)",
              "point COMFYUI_ROOT at the ComfyUI repo root (the directory containing "
              "folder_paths.py), not a subdirectory of it")
        return None
    check("ComfyUI root locatable", True, f"{source} -> {root}")
    return root


# --- 5. ComfyUI venv python + torch --------------------------------------------

_PROBE = (
    "import json\n"
    "out = {}\n"
    "try:\n"
    "    import torch\n"
    "    out['torch'] = torch.__version__\n"
    "except Exception as e:\n"
    "    out['torch_error'] = f'{type(e).__name__}: {e}'\n"
    "try:\n"
    "    import cryptography\n"
    "    out['cryptography'] = cryptography.__version__\n"
    "except Exception as e:\n"
    "    out['cryptography_error'] = f'{type(e).__name__}: {e}'\n"
    "print(json.dumps(out))\n"
)


def check_comfyui_venv(root: Path | None) -> Path | None:
    if root is None:
        check("ComfyUI venv python present", False, "skipped -- ComfyUI root not found (see check above)")
        check("torch importable in ComfyUI venv", False, "skipped -- ComfyUI root not found")
        return None

    candidates = [
        root / ".venv" / "Scripts" / "python.exe",
        root / ".venv" / "bin" / "python",
        root / "venv" / "Scripts" / "python.exe",
        root / "venv" / "bin" / "python",
    ]
    venv_python = next((p for p in candidates if p.is_file()), None)
    if venv_python is None:
        remedy = (f"create it: cd \"{root}\" && python -m venv .venv && "
                  f"\"{root / '.venv' / 'Scripts' / 'python.exe'}\" -m pip install -r requirements.txt")
        check("ComfyUI venv python present", False,
              "none of: " + ", ".join(str(c) for c in candidates), remedy)
        check("torch importable in ComfyUI venv", False, "skipped -- no venv python found")
        return None
    check("ComfyUI venv python present", True, str(venv_python))

    try:
        proc = subprocess.run([str(venv_python), "-c", _PROBE],
                               capture_output=True, text=True, timeout=30)
        data = json.loads((proc.stdout or "").strip() or "{}")
    except Exception as exc:  # noqa: BLE001 -- a preflight probe must never crash
        detail = f"probe failed to run: {type(exc).__name__}: {exc}"
        check("torch importable in ComfyUI venv", False, detail,
              f'"{venv_python}" -c "import torch"')
        check("cryptography importable in ComfyUI venv", False, detail,
              f'"{venv_python}" -c "import cryptography"')
        return venv_python

    torch_ok = "torch" in data
    check("torch importable in ComfyUI venv", torch_ok,
          f"torch {data.get('torch')}" if torch_ok else data.get("torch_error", "unknown error"),
          "" if torch_ok else f'"{venv_python}" -m pip install torch --index-url '
                               "https://download.pytorch.org/whl/cu124")

    crypto_ok = "cryptography" in data
    check("cryptography importable in ComfyUI venv (render_covenant_demo.py runs under this "
          "interpreter, not doctor.py's)", crypto_ok,
          f"cryptography {data.get('cryptography')}" if crypto_ok
          else data.get("cryptography_error", "unknown error"),
          "" if crypto_ok else f'"{venv_python}" -m pip install cryptography')
    return venv_python


# --- 6/7. checkpoints and LoRAs --------------------------------------------------

def _model_files(d: Path) -> list[str]:
    if not d.is_dir():
        return []
    return sorted(f.name for f in d.iterdir() if f.is_file() and f.suffix.lower() in CHECKPOINT_EXTS)


def check_checkpoints(root: Path | None) -> list[str]:
    if root is None:
        check("checkpoints present", False, "skipped -- ComfyUI root not found")
        return []
    d = root / "models" / "checkpoints"
    found = _model_files(d)
    remedy = ("download an SDXL base checkpoint, e.g. "
              "https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0 "
              f"and place it in {d}")
    check("checkpoints present", bool(found),
          f"found: {', '.join(found)}" if found else f"none found in {d}",
          "" if found else remedy)
    return found


def check_loras(root: Path | None) -> tuple[list[str], bool, bool]:
    if root is None:
        check("LoRAs present", False, "skipped -- ComfyUI root not found")
        check("demo LoRA: dmd2_sdxl_4step_lora_fp16.safetensors (CC-BY-NC-4.0)", False,
              "skipped -- ComfyUI root not found")
        check("demo LoRA: pixel-art-xl.safetensors (OpenRAIL-M)", False,
              "skipped -- ComfyUI root not found")
        return [], False, False

    d = root / "models" / "loras"
    found = _model_files(d)
    check("LoRAs present", bool(found),
          f"found: {', '.join(found)}" if found else f"none found in {d}",
          "" if found else f"any LoRA works for a plain render; drop one into {d}")

    has_dmd2 = any(DMD2_HINT in f.lower() for f in found)
    has_pixel = any(PIXEL_ART_HINT in f.lower() for f in found)
    check("demo LoRA: dmd2_sdxl_4step_lora_fp16.safetensors (CC-BY-NC-4.0, non-commercial)",
          has_dmd2, "" if has_dmd2 else f"not found in {d}",
          "" if has_dmd2 else f"download from https://huggingface.co/tianweiy/DMD2 into {d}")
    check("demo LoRA: pixel-art-xl.safetensors (OpenRAIL-M, commercial OK)",
          has_pixel, "" if has_pixel else f"not found in {d}",
          "" if has_pixel else f"download from https://huggingface.co/nerijs/pixel-art-xl into {d}")
    return found, has_dmd2, has_pixel


# --- 8. TSA reachability ---------------------------------------------------------

def _reachable(url: str, timeout: float = 5.0) -> tuple[bool, str]:
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return True, f"HTTP {resp.status}"
    except urllib.error.HTTPError as exc:
        # A 4xx/5xx still proves the TCP/TLS path is open -- these endpoints only
        # accept RFC 3161 POSTs, so a plain GET is expected to be refused.
        return True, f"HTTP {exc.code} (expected -- these endpoints only accept RFC 3161 POSTs)"
    except Exception as exc:  # noqa: BLE001 -- network probing must never crash the check
        return False, f"{type(exc).__name__}: {exc}"


def check_tsa_reachability() -> None:
    for name, url in (("DigiCert TSA", DIGICERT_TSA), ("Sigstore TSA", SIGSTORE_TSA)):
        ok, detail = _reachable(url)
        if ok:
            check(f"{name} reachable ({url})", True, detail)
        else:
            check(f"{name} reachable ({url})", False,
                  f"anchoring needs network -- this is NOT a broken install ({detail})",
                  "check your network/proxy, or skip anchoring entirely: "
                  "render_covenant_demo.py --no-anchor (demo_covenant.py already runs "
                  "unanchored by default)")


# --- 9. Disk space -----------------------------------------------------------------

def check_disk_space(root: Path | None, checkpoints_found: bool) -> None:
    target = root if (root and root.is_dir()) else HERE
    try:
        usage = shutil.disk_usage(str(target))
    except Exception as exc:  # noqa: BLE001
        check("free disk space", False, f"could not stat {target}: {exc}",
              "check that the drive is available")
        return
    free_gib = usage.free / (1 << 30)
    if checkpoints_found:
        check(f"free disk space ({target})", True,
              f"{free_gib:.1f} GiB free -- a checkpoint is already present, "
              "so a fresh download isn't required")
        return
    ok = free_gib >= REQUIRED_FREE_GIB_FOR_CHECKPOINT
    check(f"free disk space for a checkpoint download (>= {REQUIRED_FREE_GIB_FOR_CHECKPOINT} GiB, {target})",
          ok, f"{free_gib:.1f} GiB free",
          "" if ok else "free up disk space, or point COMFYUI_ROOT at a drive with more room")


# --- verdict -------------------------------------------------------------------

def _quote(p: Path) -> str:
    return f'"{p}"'


def verdict(*, crypto_ok: bool, smoke_covenant_ok: bool, root: Path | None,
            venv_python: Path | None, torch_ok: bool, venv_crypto_ok: bool,
            checkpoints_found: list[str], has_dmd2: bool, has_pixel: bool) -> int:
    banner("VERDICT")

    prefix = _script_prefix()  # "covenant/" in-tree, "" in the standalone repo

    # demo_covenant.py needs no ComfyUI/torch -- just cryptography + smoke_covenant
    # importable on WHATEVER interpreter runs it. That can be this doctor's own
    # host interpreter, or (just as validly) ComfyUI's venv python, if that venv
    # already has cryptography. Gating this on the host alone under-reports what
    # is actually runnable when the two environments differ, which is common.
    demo_via_host = crypto_ok and smoke_covenant_ok
    demo_via_venv = venv_python is not None and venv_crypto_ok
    demo_covenant_ok = demo_via_host or demo_via_venv
    demo_cmd = (f"python {prefix}demo_covenant.py" if demo_via_host
                else f"{_quote(venv_python)} {prefix}demo_covenant.py" if demo_via_venv
                else None)
    print(f"demo_covenant.py  (no ComfyUI/models/GPU needed): "
          f"{'RUNNABLE  ->  ' + demo_cmd if demo_covenant_ok else 'BLOCKED'}")
    if demo_via_venv and not demo_via_host:
        print("  (this interpreter lacks cryptography; the ComfyUI venv already has it, hence the command above)")

    # The render demos run entirely under the ComfyUI venv python (imports torch,
    # folder_paths, nodes), so they depend on THAT interpreter's cryptography, not
    # the host's -- not on demo_via_host / smoke_covenant_ok at all.
    render_prereqs_ok = (root is not None and venv_python is not None
                         and torch_ok and venv_crypto_ok)
    has_ckpt = bool(checkpoints_found)
    py = _quote(venv_python) if venv_python else '"<ComfyUI venv python>"'

    cases = [
        ("Case A (paid ad + DMD2, expects BLOCKED)",
         render_prereqs_ok and has_ckpt and has_dmd2,
         f"{py} {prefix}render_covenant_demo.py --lora dmd2"),
        ("Case B (paid ad + pixel-art, expects a real render)",
         render_prereqs_ok and has_ckpt and has_pixel,
         f"{py} {prefix}render_covenant_demo.py --lora pixel-art"),
        ("Case C (--non-commercial + DMD2, expects admitted)",
         render_prereqs_ok and has_ckpt and has_dmd2,
         f"{py} {prefix}render_covenant_demo.py --lora dmd2 --non-commercial"),
    ]
    for label, ok, cmd in cases:
        print(f"  {label}: {'RUNNABLE' if ok else 'blocked'}")
        print(f"    {cmd}")

    print()
    if not demo_covenant_ok:
        next_cmd = f'"{sys.executable}" -m pip install cryptography'
        print(f"Nothing is runnable yet. Next command:\n  {next_cmd}")
        print(f"  (then re-run: python {prefix}doctor.py)")
        return 1

    any_case = any(ok for _, ok, _ in cases)
    if not any_case:
        print(f"Only demo_covenant.py is runnable right now. Next command:\n  {demo_cmd}")
        missing = []
        if root is None:
            missing.append("a ComfyUI checkout (set COMFYUI_ROOT)")
        elif venv_python is None:
            missing.append("ComfyUI's venv (create .venv and install its requirements)")
        elif not torch_ok:
            missing.append("torch in that venv")
        elif not venv_crypto_ok:
            missing.append("cryptography in that venv")
        if not has_ckpt:
            missing.append("a checkpoint in models/checkpoints")
        if not (has_dmd2 or has_pixel):
            missing.append("at least one of the two demo LoRAs")
        if missing:
            print(f"  (to unlock the real-render demos, still needed: {'; '.join(missing)})")
        return 0

    # Prefer showing the genuine render (Case B) when it's available; otherwise
    # whichever real-render case is runnable.
    best = next((cmd for label, ok, cmd in cases if ok and "Case B" in label), None)
    if best is None:
        best = next(cmd for _, ok, cmd in cases if ok)
    print(f"Next command:\n  {best}")
    return 0


# --- main ------------------------------------------------------------------------

def main() -> int:
    banner("SMOKE RENDER COVENANT -- PREFLIGHT DOCTOR")

    banner("1-3. Interpreter and library")
    check_python_version()
    crypto_ok = check_cryptography()
    smoke_covenant_ok = check_smoke_covenant()

    banner("4-5. ComfyUI")
    root = check_comfyui_root()
    venv_python = check_comfyui_venv(root)
    torch_ok = any(r["name"] == "torch importable in ComfyUI venv" and r["ok"] for r in results)
    venv_crypto_ok = any(
        r["name"].startswith("cryptography importable in ComfyUI venv") and r["ok"] for r in results
    )

    banner("6-7. Models")
    checkpoints_found = check_checkpoints(root)
    _, has_dmd2, has_pixel = check_loras(root)

    banner("8. Anchoring network reachability")
    check_tsa_reachability()

    banner("9. Disk space")
    check_disk_space(root, bool(checkpoints_found))

    return verdict(
        crypto_ok=crypto_ok, smoke_covenant_ok=smoke_covenant_ok, root=root,
        venv_python=venv_python, torch_ok=torch_ok, venv_crypto_ok=venv_crypto_ok,
        checkpoints_found=checkpoints_found, has_dmd2=has_dmd2, has_pixel=has_pixel,
    )


if __name__ == "__main__":
    raise SystemExit(main())
