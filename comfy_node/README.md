# Render Covenant — ComfyUI custom node

Issues a **Render Covenant** over an image ComfyUI produced: a signed,
offline-verifiable statement binding the exact master bytes to the licensed
assets the render *actually loaded*, checked against a policy at the moment of
use rather than declared in a spreadsheet afterwards.

The refusal is the product. An asset that does not resolve to an acceptable
grant stops the render; it is not logged and rendered anyway.

Verified against **ComfyUI @806e092 (v0.28.0)**.

## Licensing

ComfyUI is GPL-3.0. **This package vendors, patches and redistributes none of
it.** It is a separate work that ComfyUI loads and that hooks ComfyUI's public
extension points at runtime — the established custom-node pattern. Every
ComfyUI import is deferred to load or call time inside ComfyUI's own process.
The engineering agrees with the licence: a runtime hook survives ComfyUI updates
that a source patch would not.

## Install

The package lives in the smoke-suite repo and is linked into ComfyUI, so there
is one copy and no drift.

```bat
:: Windows (junction — no admin needed)
mklink /J "C:\Users\me\ComfyUI\custom_nodes\smoke_render_covenant" ^
          "C:\path\to\smoke-suite\covenant\comfy_node"
```

```sh
# Linux / macOS
ln -s /path/to/smoke-suite/covenant/comfy_node \
      ~/ComfyUI/custom_nodes/smoke_render_covenant
```

`smoke_covenant` and `smoke_trust` are found by walking up from the resolved
`__file__`, which works through the link. If you copy the directory instead of
linking it, set `SMOKE_COVENANT_SUITE=/path/to/smoke-suite`.

Then restart ComfyUI and add **Render Covenant (Issue)** (`SmokeCovenantIssue`)
where you would normally put Save Image. It writes the master PNG itself — see
[Outputs](#outputs).

**Start ComfyUI with `--cache-none`.** Without it, re-queueing a workflow serves
loader nodes from cache, they never resolve their files, and the gate cannot see
them. The node detects this and refuses to issue (see
[Coverage](#coverage--what-escapes-the-gate) §1), so the failure is loud rather
than silent — but `--cache-none` is what makes the workflow usable.

## Config format

One JSON file, named by the node's `config_path` widget. Relative paths inside
it resolve against the config file's own directory.

```json
{
  "policy": "media_licence",
  "strict": true,
  "record_only": false,
  "audit_escapes": false,
  "thread_affinity": true,
  "staging_dir": "D:/covenant-staging",
  "output_dir": null,
  "signing_key_pem": "D:/keys/studio-signing.pem",
  "anchor": false,
  "context": {
    "production": "Campaign-482",
    "territory": "US",
    "channels": ["paid-social"],
    "release_end": "2027-02-01",
    "commercial": true,
    "intended_uses": ["advertising"]
  },
  "assets": [
    {
      "path": "../../ComfyUI/models/checkpoints/sd_xl_base_1.0.safetensors",
      "label": "sd_xl_base_1.0.safetensors",
      "grant_id": "licence:CreativeML OpenRAIL++-M",
      "kind": "model_licence",
      "signer_spki": "huggingface:stabilityai",
      "licence": "creativeml-openrail++-m"
    },
    {
      "path": "D:/dam/hero-plate.png",
      "kind": "asset_license",
      "terms": { "territories": ["US"], "expires_on": "2028-01-01" }
    }
  ]
}
```

| key | default | meaning |
|---|---|---|
| `policy` | `media_licence` | `media_licence` (commercial-use + use-restrictions + territory/date/channel) or `toy_territory_window` (labelled demo stub) |
| `context` | **required** | the render's facts, read by the policy |
| `assets` | **required, non-empty** | what the studio has cleared. Each entry needs `path` plus exactly one of `licence` (a key of `smoke_covenant.policies.LICENCE_TERMS`) or `terms` (inline) |
| `strict` | `true` | `false` marks the covenant `hermetic: false` instead of refusing on an escape |
| `record_only` | `false` | admit everything, record what *would* have been refused. Marks the render non-hermetic. Adoption ramp only |
| `audit_escapes` | `false` | install the `builtins.open` probe. Read the warning in §5 first |
| `thread_affinity` | `true` | confine the gate to the render thread. See §3 |
| `staging_dir` | none | content-addressed staging. **Set this** — it is the only fully TOCTOU-free path (see `gate.admit_staged`). Must be somewhere your asset writers cannot write |
| `output_dir` | ComfyUI's output dir | where masters and covenants go. A custom dir disables the UI thumbnail |
| `signing_key_pem` | none | PEM EC private key. Without it each run signs with an **ephemeral** key that verifies bytes but binds no identity |
| `anchor` | `false` | request RFC 3161 trusted-time witnesses. Needs network; a TSA outage then fails the render |
| `renderer_identity` | `{}` | extra fields folded into the covenant's `renderer` block |

Registration hashes every declared asset, so a config naming a path that no
longer exists fails at prompt start rather than mid-render.

## Outputs

For each image in the batch, beside each other in `output_dir`:

```
master_00001_.png              the covenanted bytes, written with NO metadata
master_00001_.covenant.json    the signed covenant
master_00001_.pubkey.pem       the verifying key (convenience, NOT a trust root)
```

The node writes the PNG itself rather than chaining off Save Image, because a
covenant binds a digest: Save Image embeds the workflow as PNG metadata, and
anything that later rewrites or strips it would break a covenant taken over the
annotated file.

The public key travelling with the artefact proves only self-consistency. A
verifier must pin the signer out of band. `anchor: false` also means
`verify(..., require_anchor=True)` — the default — will refuse the covenant:
without trusted time it carries only the signer's word about when it was issued.

## The execution seam

In library mode a driver owns the render and `with covenant_gate(gate):`
brackets it exactly. In server mode nothing owns the render — ComfyUI's worker
thread pulls a prompt off a queue and walks a DAG, and a node only ever sees its
own call. A gate opened and closed inside one node would cover that node and
nothing else, which would be a lie told with a signature on it.

ComfyUI does offer a real per-prompt bracket, and it is a **public extension
point** rather than an internal:

```
comfy_execution.cache_provider.register_cache_provider(provider)
    provider.on_prompt_start(prompt_id)   execution.py:739  — before the node loop
    provider.on_prompt_end(prompt_id)     execution.py:833  — inside `finally`
```

That is the whole prompt, once, with guaranteed teardown even on a node error or
an interrupt. The interface is `CacheProvider` because that is where ComfyUI
hung the hooks; we are not a cache — `should_cache()` returns `False`, which
short-circuits `on_lookup` and `on_store` at their call sites
(`comfy_execution/caching.py:293` and `:259`). Nothing here participates in
caching semantics.

Three things the seam does not give us, and what is done instead:

- **It cannot refuse.** `_notify_prompt_lifecycle` swallows exceptions and logs a
  warning (execution.py:721-722), so an arming failure cannot stop the prompt.
  Failures are recorded on the session and the **issuing node** raises. Fail-closed
  moves; it does not disappear.
- **It passes only a `prompt_id`.** The config path is a node widget, so the
  running graph is read out of `PromptQueue.currently_running`, which is
  populated before `execute()` is called (execution.py:1269, main.py:359). No
  covenant node in the graph means nothing is armed and nothing is patched.
- **It knows nothing about the output cache.** Handled at the node — see §1 below.

## Coverage — what escapes the gate

**An escape invalidates the hermetic claim rather than weakening it.** If bytes
entered the render without crossing the gate, the covenant proves nothing about
them, and a covenant that is signed, timestamped and *wrong* is worse than none.
Read this section as part of the claim, not as a footnote to it.

### What IS covered

Everything that routes through, **on the thread ComfyUI executes the prompt on**:

- `folder_paths.get_full_path` — checkpoints, LoRAs, VAEs, ControlNets, CLIP,
  UNets, upscalers, style models… the folder name becomes the ingredient's role.
  `get_full_path_or_raise` calls it internally and so is covered by the same
  single patch.
- `folder_paths.get_annotated_filepath` — `LoadImage` and friends.
- `comfy.sd1_clip.load_embed` — textual-inversion embeddings, which reach the
  loader as a *directory list* and so never touch `get_full_path`.

### What is NOT covered

1. **A loader served from ComfyUI's output cache.** CLASSIC, LRU and the default
   RAM_PRESSURE caches all persist node outputs *across prompts*. A cached
   `CheckpointLoaderSimple` does not run, so it never resolves its file and the
   gate never sees the checkpoint — for a render that unmistakably contains it.
   **Mitigated, not eliminated:** the node walks the static prompt graph
   backwards from itself and requires every asset its lineage names to appear
   among the admitted ingredients. A miss is a refusal. The check is blind to
   nodes created by runtime subgraph expansion (they are not in the static
   prompt) — those are still gated if they resolve through `folder_paths`, but
   completeness is not guaranteed for them. Run with `--cache-none`.

2. **A custom node that reads a path it built itself.** `open()`, `torch.load()`,
   `safetensors.load_file()` on a path assembled inside the node bypasses
   `folder_paths` entirely. Nothing here can see it. This is the same
   irreducible gap the library-mode adapter has.

3. **Anything resolved on a thread the node spawned itself.** The patches are
   process-wide, and ComfyUI's aiohttp thread calls the very functions we patch
   (`server.py:665`, `/view_metadata`). Left alone, a user browsing models in the
   web UI during a render would push refusals into that render's gate and kill an
   unrelated covenant. So the patches dispatch to the *original* function off the
   render thread. The cost is a coverage gap library mode does not have: a node
   that loads assets from its own worker pool escapes. Off-thread resolutions are
   counted and printed on the node's output so they are visible rather than
   assumed absent. Set `thread_affinity: false` to gate every thread and accept
   the web-UI contamination instead.

4. **A node that fetches a URL, reads a database, or receives bytes over an API
   at runtime.** No file resolution happens, so there is nothing to intercept.

5. **`audit_escapes` is a diagnostic, not a guarantee.** It watches `builtins.open`
   for weight-shaped files under the model roots. It finds honest mistakes; it
   does not stop a node determined to read around the gate. It also patches
   `builtins.open` for the whole process, and in strict mode a hit *raises* — so
   with `thread_affinity: false` it can raise inside ComfyUI's web server. Leave
   it off unless you are actively auditing a workflow.

6. **Ingredients are prompt-scoped, not per-image-lineage-scoped.** The gate
   records every asset admitted during the prompt. Two independent branches in
   one prompt each contribute ingredients to *both* covenants. That over-states
   (you claim to have used something you did not) rather than under-states, so it
   cannot be used to hide an asset — but it is not precise. **Use one covenant
   node per prompt.**

7. **Everything `smoke_covenant` itself disclaims.** It does not prove an asset
   was lawfully obtained, that a grant signer told the truth or held authority,
   that a model's training set was clean, or that a court reads a licence the way
   the config's `terms` do. A human read those licences and transcribed them; this
   re-applies that reading consistently and commits to the result. See the
   non-goals in `smoke_covenant/grants.py`.

## Testing

```
C:/Users/topdy/ComfyUI/.venv/Scripts/python.exe covenant/test_comfy_node.py
```

Covers the mapping shape, provider registration, the open/close bracket and its
patch restoration, arming failures, refusal of an ungranted asset, thread
affinity, the completeness check, and an end-to-end issue-then-verify with a
tamper check. No GPU or real checkpoint required.
