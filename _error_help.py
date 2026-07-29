"""Shared "friendly failure" formatting for the covenant demo/test scripts.

NOT part of the public `smoke_covenant` package (see smoke_covenant/__init__.py)
-- this is plumbing for the CLI scripts that sit next to it (demo_covenant.py,
render_covenant_demo.py, the test_*.py scripts), so a new user who hits a
missing dependency gets a diagnosis and an exact remedy command instead of a
raw traceback as the only output.

The failure itself is NEVER hidden or softened: this project is fail-closed by
design, and a diagnostic that swallows or downgrades an error would just be a
quieter bug. `missing_dependency` still exits non-zero (matching what an
uncaught exception would have done) and still prints the original exception
text -- it only adds a plain-English "what broke" line and a "how to fix it"
line ahead of it.

See covenant/doctor.py for the full preflight check this grew out of: run
that first and most of these should never fire.
"""

from __future__ import annotations

import sys


def _doctor_prefix() -> str:
    """Mirrors doctor.py's own layout detection ("covenant/" inside the
    smoke-suite monorepo, "" in the standalone public repo, where this file
    IS the repo root -- see `_paths.script_prefix()`). Every caller of
    `missing_dependency` has already done `sys.path.insert(0, ...)` for this
    directory and imported `_paths` before importing this module, so the
    import below should always succeed; the fallback to the old hardcoded
    "covenant/" only guards against that assumption someday not holding."""
    try:
        import _paths  # type: ignore
        return _paths.script_prefix()
    except Exception:
        return "covenant/"


def missing_dependency(exc: BaseException, *, what: str, remedy: str) -> None:
    """Report a missing import as a diagnosis + remedy, then exit(1).

    `what` is a one-line plain-English statement of what is missing and why it
    matters here. `remedy` is the exact command (or short instruction) that
    fixes it. The original exception's type and message are always printed too
    -- never hidden -- so nothing is lost relative to the raw traceback.
    """
    # Plain stdout, matching every other diagnostic these scripts print (and
    # deliberately NOT stderr): stdout is line-buffered/flushed by `print()` in
    # the same order things actually happened, whereas splitting this one
    # message onto stderr reorders it ahead of earlier stdout output whenever
    # both streams are merged (a redirected run, a captured subprocess, a CI
    # log) -- confusing right when clarity matters most.
    print("\n[MISSING DEPENDENCY]")
    print(f"  {what}")
    print(f"  ({type(exc).__name__}: {exc})")
    print(f"\n  Fix: {remedy}")
    print(f"\n  For the full picture: python {_doctor_prefix()}doctor.py\n")
    raise SystemExit(1)
