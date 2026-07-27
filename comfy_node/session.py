"""Hold the hermetic gate open for exactly one ComfyUI prompt execution.

THE SEAM, and why this one.
In library mode the driver owns the whole render, so `with covenant_gate(gate):`
brackets it exactly. In server mode nothing owns the render: ComfyUI's worker
thread pulls a prompt off a queue and walks a DAG of node calls, and a node only
ever sees its own call. A gate opened and closed inside one node would cover that
node and nothing else, which would be a lie told with a signature on it.

ComfyUI @806e092 does offer a real per-prompt bracket, and it is a PUBLIC
extension point rather than an internal we would be monkeypatching:

    comfy_execution.cache_provider.register_cache_provider(provider)
        -> provider.on_prompt_start(prompt_id)   execution.py:739
        -> provider.on_prompt_end(prompt_id)     execution.py:833, inside `finally`

`on_prompt_start` fires after `execution_start` and BEFORE the `try:` that
contains the entire node-execution loop; `on_prompt_end` fires from that block's
`finally`, so the patches come off even when a node raises or the user hits
interrupt. That is the whole prompt, once, with guaranteed teardown.

The interface is `CacheProvider` because that is where ComfyUI hung the hooks.
We are not a cache: `should_cache` returns False, which short-circuits both
`on_lookup` and `on_store` at their call sites (comfy_execution/caching.py:293
and :259). Nothing we do participates in caching semantics.

FOUR THINGS THE SEAM DOES NOT GIVE US, stated because the package's claim is
only worth what its coverage statement is worth:

1. IT CANNOT REFUSE. `_notify_prompt_lifecycle` wraps the call in
   `try/except Exception` and logs a warning (execution.py:721-722). An
   exception raised here does not stop the prompt. So arming failures are
   RECORDED on the session and the refusal happens at the issuing node, which
   can stop things. Fail-closed moves; it does not disappear.

2. IT PASSES ONLY A prompt_id. The config path lives on a node widget, so we
   read the running graph out of the queue -- `PromptQueue.get()` inserts into
   `currently_running` before `prompt_worker` calls `execute()` (execution.py:1269,
   main.py:359), so the graph is there. No graph, or no covenant node in it,
   means we do not arm and do not patch anything.

3. IT IS PROCESS-WIDE, AND SO ARE THE PATCHES. ComfyUI's aiohttp thread calls
   the very functions we patch (server.py:665, `/view_metadata`). Left alone
   that thread's browsing would push refusals into a render's gate and kill an
   unrelated covenant. So the patches are confined to the thread ComfyUI runs
   the prompt on. The cost is a NEW coverage gap that library mode does not
   have -- see README COVERAGE -- and it is counted, not hidden: off-thread
   resolutions are tallied and reported on the node's output.

4. IT KNOWS NOTHING ABOUT ComfyUI'S OUTPUT CACHE. That is the sharpest hole in
   server mode and it is handled at the issuing node, not here. See
   `issue_node.completeness` -- a cached loader never calls folder_paths, so the
   gate would record nothing for a model that is genuinely in the render.
"""

from __future__ import annotations

import builtins
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

from smoke_covenant import HermeticGate
from smoke_covenant.adapters.comfy import covenant_gate

from .config import CovenantConfig, load_config

# Must equal the NODE_CLASS_MAPPINGS key in __init__.py -- this is how the
# session finds its config before any node has run.
NODE_ID = "SmokeCovenantIssue"

try:
    from comfy_execution.cache_provider import (  # type: ignore
        CacheProvider,
        register_cache_provider,
    )

    SEAM_ERROR: str | None = None
except Exception as _exc:  # pragma: no cover - depends on the host ComfyUI
    CacheProvider = object  # type: ignore[assignment,misc]
    register_cache_provider = None  # type: ignore[assignment]
    SEAM_ERROR = (
        f"comfy_execution.cache_provider is unavailable ({_exc}); this ComfyUI "
        "offers no per-prompt lifecycle hook, so the gate cannot span a prompt "
        "and no covenant can be issued in server mode"
    )

_MAX_OFFTHREAD_LABELS = 32


class _ThreadAffinity:
    """Confine the adapter's patches to one thread.

    Wraps whatever `covenant_gate` installed so that calls from other threads
    reach the ORIGINAL function instead. No uninstall method exists and none is
    needed: every teardown path in the adapter assigns its captured original
    unconditionally, so exiting the gate discards these wrappers wholesale.
    """

    def __init__(self, owner_thread: int, targets: list[tuple[Any, str]]) -> None:
        self._owner = owner_thread
        self._targets = targets
        self._pristine: dict[tuple[int, str], Callable] = {}
        self.offthread: dict[str, int] = {}

    def capture(self) -> None:
        """Snapshot the unpatched callables. MUST run before the gate is entered."""
        for mod, attr in self._targets:
            self._pristine[(id(mod), attr)] = getattr(mod, attr)

    def install(self) -> None:
        """Replace the gate's patches with thread-dispatching wrappers."""
        for mod, attr in self._targets:
            key = (id(mod), attr)
            if key not in self._pristine:
                continue
            setattr(
                mod,
                attr,
                self._wrap(getattr(mod, attr), self._pristine[key],
                           f"{getattr(mod, '__name__', mod)}.{attr}"),
            )

    def _wrap(self, gated: Callable, pristine: Callable, label: str) -> Callable:
        owner, tally = self._owner, self.offthread

        def dispatch(*args, **kwargs):
            if threading.get_ident() == owner:
                return gated(*args, **kwargs)
            if label in tally or len(tally) < _MAX_OFFTHREAD_LABELS:
                tally[label] = tally.get(label, 0) + 1
            return pristine(*args, **kwargs)

        return dispatch


@dataclass
class PromptSession:
    """One prompt's gate, plus whatever went wrong arming it."""

    prompt_id: str
    owner_thread: int
    config_path: str | None = None
    config: CovenantConfig | None = None
    gate: HermeticGate | None = None
    error: str | None = None
    _cm: Any = None
    _affinity: _ThreadAffinity | None = None
    _closed: bool = field(default=False)

    @property
    def armed(self) -> bool:
        return self.gate is not None and self.error is None

    @property
    def offthread_resolutions(self) -> dict[str, int]:
        return dict(self._affinity.offthread) if self._affinity else {}

    def close(self) -> None:
        """Exit the gate. Idempotent, and never raises out of teardown."""
        if self._closed:
            return
        self._closed = True
        cm, self._cm = self._cm, None
        if cm is not None:
            try:
                cm.__exit__(None, None, None)
            except Exception as exc:  # noqa: BLE001 - teardown must complete
                self.error = self.error or f"gate teardown raised: {exc}"


class CovenantSessionProvider(CacheProvider):  # type: ignore[misc,valid-type]
    """Lifecycle-only ComfyUI cache provider. Caches nothing, brackets prompts."""

    def __init__(self, graph_resolver: Callable[[str], Any] | None = None) -> None:
        self._resolve_graph = graph_resolver or running_prompt_graph
        self._lock = threading.RLock()
        self._sessions: dict[str, PromptSession] = {}

    # --- cache protocol: declined in full ---------------------------------

    def should_cache(self, context, value=None) -> bool:  # noqa: ANN001
        """False short-circuits on_lookup and on_store at their call sites.
        A rights gate has no business influencing what ComfyUI reuses."""
        return False

    async def on_lookup(self, context):  # noqa: ANN001
        return None

    async def on_store(self, context, value) -> None:  # noqa: ANN001
        return None

    # --- the actual seam ---------------------------------------------------

    def on_prompt_start(self, prompt_id: str) -> None:
        """Arm a gate for this prompt if its graph asks for one."""
        try:
            graph = self._resolve_graph(prompt_id)
        except Exception as exc:  # noqa: BLE001 - never break the host
            graph = None
            self._record_blind(prompt_id, f"could not read the running graph: {exc}")
            return
        paths = config_paths_in_graph(graph)
        if not paths:
            return  # not a covenanted prompt: patch nothing

        session = PromptSession(prompt_id=prompt_id, owner_thread=threading.get_ident(),
                                config_path=paths[0])
        with self._lock:
            self._sessions[prompt_id] = session
        try:
            self._arm(session, paths)
        except Exception as exc:  # noqa: BLE001 - the node re-raises this
            session.error = f"{type(exc).__name__}: {exc}"
            session.close()

    def on_prompt_end(self, prompt_id: str) -> None:
        with self._lock:
            session = self._sessions.pop(prompt_id, None)
        if session is not None:
            session.close()

    def session(self, prompt_id: str) -> PromptSession | None:
        with self._lock:
            return self._sessions.get(prompt_id)

    def current_session(self) -> PromptSession | None:
        """The session armed on THIS thread.

        Nodes are handed PROMPT and UNIQUE_ID but never a prompt_id, so identity
        comes from the thread instead -- which is in fact the stronger link: the
        session was armed on the thread ComfyUI executes prompts on, and this
        node is running on it. A node that somehow ran on another thread finds no
        session and refuses, which is the correct answer for a render whose
        loads were not being gated.
        """
        me = threading.get_ident()
        with self._lock:
            matches = [s for s in self._sessions.values() if s.owner_thread == me]
        if len(matches) > 1:
            raise RuntimeError(
                f"{len(matches)} covenant sessions are armed on this thread; "
                "ComfyUI is expected to execute one prompt at a time per worker, "
                "so refusing to guess which render is being covenanted"
            )
        return matches[0] if matches else None

    # --- internals ---------------------------------------------------------

    def _arm(self, session: PromptSession, paths: list[str]) -> None:
        distinct = sorted(set(paths))
        if len(distinct) > 1:
            raise ValueError(
                "this prompt carries covenant nodes with different config_path "
                f"values ({distinct}); one prompt is one gate, so refusing to "
                "guess which rights context applies"
            )
        cfg = load_config(distinct[0])
        session.config = cfg
        gate = HermeticGate(
            cfg.store, cfg.policy, cfg.context,
            strict=cfg.strict, staging_dir=cfg.staging_dir,
        )

        affinity = None
        if cfg.thread_affinity:
            affinity = _ThreadAffinity(session.owner_thread,
                                       _patch_targets(cfg.audit_escapes))
            affinity.capture()

        cm = covenant_gate(gate, record_only=cfg.record_only,
                           audit_escapes=cfg.audit_escapes)
        cm.__enter__()
        session._cm = cm
        if affinity is not None:
            affinity.install()
            session._affinity = affinity
        session.gate = gate

    def _record_blind(self, prompt_id: str, message: str) -> None:
        """Remember a failure that happened before we could see the graph.

        Registered unconditionally: if this prompt did contain a covenant node,
        that node must fail closed rather than find no session and wonder why.
        """
        with self._lock:
            self._sessions[prompt_id] = PromptSession(
                prompt_id=prompt_id, owner_thread=threading.get_ident(), error=message
            )


def _patch_targets(audit_escapes: bool) -> list[tuple[Any, str]]:
    """The attributes `covenant_gate` replaces, in the same order it replaces them."""
    import folder_paths
    import comfy.sd1_clip as sd1

    targets: list[tuple[Any, str]] = [
        (folder_paths, "get_full_path"),
        (folder_paths, "get_annotated_filepath"),
        (sd1, "load_embed"),
    ]
    if audit_escapes:
        targets.append((builtins, "open"))
    return targets


def running_prompt_graph(prompt_id: str) -> dict | None:
    """The API-format graph of the prompt ComfyUI is executing right now."""
    from server import PromptServer  # ComfyUI, imported late and never vendored

    instance = getattr(PromptServer, "instance", None)
    queue = getattr(instance, "prompt_queue", None)
    if queue is None:
        return None
    running, _ = queue.get_current_queue_volatile()
    for item in running:
        # item = (number, prompt_id, prompt, extra_data, outputs_to_execute, ...)
        if len(item) > 2 and item[1] == prompt_id:
            return item[2]
    return None


def config_paths_in_graph(graph: Any) -> list[str]:
    """Every config_path named by a covenant node in this graph."""
    if not isinstance(graph, dict):
        return []
    found = []
    for node in graph.values():
        if not isinstance(node, dict) or node.get("class_type") != NODE_ID:
            continue
        value = (node.get("inputs") or {}).get("config_path")
        if isinstance(value, str) and value.strip():
            found.append(value.strip())
    return found


# --- process-wide registration ----------------------------------------------

_PROVIDER: CovenantSessionProvider | None = None
_INSTALL_LOCK = threading.Lock()


def install() -> str:
    """Register the lifecycle provider once per PROCESS. Returns a status line.

    Once per process, not once per module: two copies of this package installed
    under custom_nodes are two module objects with two module globals, and each
    would register its own provider. Both would then arm a gate for the same
    prompt and patch folder_paths on top of each other -- the second gate seeing
    the first gate's patches as its originals, so teardown would restore a patched
    function and leak. So a copy that finds an already-registered provider adopts
    it instead of adding another.
    """
    global _PROVIDER
    if SEAM_ERROR is not None:
        return f"NOT INSTALLED: {SEAM_ERROR}"
    with _INSTALL_LOCK:
        if _PROVIDER is not None:
            return "per-prompt gate seam already installed"
        existing = _registered_covenant_provider()
        if existing is not None:
            _PROVIDER = existing
            return ("adopted the per-prompt gate seam registered by another copy "
                    "of this package; only one gate will be armed per prompt")
        _PROVIDER = CovenantSessionProvider()
        register_cache_provider(_PROVIDER)
    return "per-prompt gate seam installed (CacheProvider lifecycle hooks)"


def _registered_covenant_provider():
    """A provider of this class already registered, possibly by another copy.

    Matched by class NAME rather than identity, because a second copy of the
    package is a different module object and therefore a different class.
    """
    from comfy_execution.cache_provider import _get_cache_providers

    for candidate in _get_cache_providers():
        if type(candidate).__name__ == CovenantSessionProvider.__name__:
            return candidate
    return None


def provider() -> CovenantSessionProvider | None:
    return _PROVIDER
