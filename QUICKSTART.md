# Render Covenant — QUICKSTART

You have never seen this project before. This document gets you to a working
demo, then to the real thing, and tells you exactly what the result does and
does not mean.

Two independent tracks:

- **Track 1** — no ComfyUI, no models, no GPU. One dependency (`cryptography`),
  one script, under a minute. Start here.
- **Track 2** — a real ComfyUI diffusion render, gated for real, ending in a
  signed covenant over a real PNG. Needs ComfyUI, an SDXL checkpoint (~6.5 GiB)
  and two small LoRAs.

Commands below are written relative to the root of your checkout, with forward
slashes throughout so the Windows forms paste as-is into **either PowerShell or
Git Bash**. Where a path points outside this repo (a ComfyUI checkout, a model
file), substitute your own.

Optional but recommended, before either track: run the built-in preflight
check. It is stdlib-only (nothing to install first) and tells you exactly
what's missing and the exact command to fix it, ending in a VERDICT of which
demos are runnable right now:

```
python doctor.py
```

---

## Track 1 — see it work in 60 seconds

**What this proves end to end:** register a few "rights grants," render
through a gate that can refuse, sign the result, verify it offline, disclose
one ingredient without revealing the rest, then watch two different kinds of
refusal and a tamper detection. No ComfyUI, no model weights, no GPU — the
"renderer" is nine lines of stand-in code in `demo_covenant.py` that reads
byte strings instead of real media.

### Prerequisite

Python **3.11+** on your PATH. It has not been independently tested below that.
Verified in this pass on Python 3.12.13.

### Run it

From the root of your checkout:

```sh
# Linux / macOS
python -m venv .venv
./.venv/bin/python -m pip install cryptography
./.venv/bin/python demo_covenant.py
```

```sh
# Windows (Git Bash or PowerShell)
python -m venv .venv
./.venv/Scripts/python.exe -m pip install cryptography
./.venv/Scripts/python.exe demo_covenant.py
```

**This was run for real while writing this document**, using a brand-new
virtual environment with only `cryptography` installed and nothing else on
`PYTHONPATH` — see "How this was verified" at the end of this file. The output
below is the genuine result, not a mock-up. Digests are deterministic (the
demo's inputs are fixed byte strings), so you should see the same hex prefixes
if you run it yourself; the temp directory name will differ.

```
========================================================================
1. CLEAN RENDER — every ingredient resolves to a grant
========================================================================
  [OK] render completed, 3 ingredients recorded from ACTUAL READS
       lora         brand_v3.safetensors     model_grant_204
       music        track04.wav              sync_license_118
       photograph   surfer.jpg               asset_license_774
  [OK] covenant issued
       master root  8bf84c54185bed025ecbcb7e69223f7a...
       ingredient   44e8a057449bc8e4743ffda5f0614b0e...
       hermetic     True

========================================================================
2. DISTRIBUTOR VERIFIES — offline, no contact with the issuer
========================================================================
  [BLOCKED] default verify refuses it: covenant carries no trusted-time anchor, so its issue time rests entirely on the signer's own word
  [OK] signature + master digest verify against the delivered file
       anchored=False — trusted time NOT established here

========================================================================
3. SELECTIVE DISCLOSURE — reveal ONE grant, keep the rest sealed
========================================================================
  [OK] proved asset_license_774 was an input, revealing 1 sibling hashes
       the other 2 ingredients stay undisclosed

========================================================================
4. THE MONEY SHOT — swap in an unlicensed LoRA
========================================================================
  [BLOCKED] render refused: no registered asset for digest 0bb83191daed36d8... -- an unregistered asset cannot participate in a render
       the unlicensed asset never reached the renderer

========================================================================
5. WRONG TERRITORY — the photo is US-only, campaign goes UK
========================================================================
  [BLOCKED] render refused: policy TOY-territory-window-v0 refused grant asset_license_774 (asset_license) for asset c8f983070bc4fa03...

========================================================================
6. NO COVENANT OVER A DENIED RENDER
========================================================================
  [BLOCKED] refusing to issue: the gate denied 1 asset(s); first was ...surfer.jpg (PolicyDenied: policy TOY-territory-window-v0 refused grant asset_license_774 ...)

========================================================================
7. ONE BYTE CHANGES — the old covenant no longer applies
========================================================================
  [BLOCKED] master digest mismatch: covenant binds 8bf84c54185bed02... but the delivered file hashes to ba73be327724bedb... -- this covenant does not cover these bytes

========================================================================
WHAT THIS DID AND DID NOT PROVE
========================================================================
  PROVED: a policy ran over the ingredients the render ACTUALLY READ through a
          gate that could refuse, and the result is bound to exactly these bytes,
          verifiable by a third party with no access to the issuer.

  DID NOT PROVE: that an asset was lawfully obtained, that a grant signer told the
          truth or held authority, that a generator's training set was clean, or
          that a cropped/re-encoded copy would be recognised. The bundled policy is
          a labelled TOY. A read that bypasses the gate is outside the claim
          entirely -- it does not weaken it, it invalidates it.

  artifacts: C:\Users\...\Temp\covenant-demo-XXXXXXXX
```

### What each section means

**1. CLEAN RENDER.** Three files are created on disk (a "photo", a "music"
track, a "LoRA" — all just literal byte strings; nothing here is a real
image). Each is registered in an `AssetStore` with a `Grant`: a licence-shaped
record (territories, an expiry date, allowed channels). The stand-in
"renderer" then reads each file through `HermeticGate.open_asset()` — the
**only** way it is allowed to read anything. Every read resolves to a grant
the policy accepts, so all three become recorded *ingredients*. `issue()`
then: builds a Merkle root over the ingredient list, hashes the rendered
"master" file, and signs `{master digest, ingredient root, policy, decision}`
as one canonical body with a P-256 key (here, an ephemeral one generated
just for this run — it verifies the signature but proves no identity).

**2. DISTRIBUTOR VERIFIES.** `verify()` needs no network call and no
cooperation from whoever issued the covenant — it only needs the covenant
JSON, the delivered file, and the issuer's public key. By default it also
requires a trusted-time anchor (an RFC 3161 timestamp from an independent
authority); this demo never contacts one (it is designed to run with zero
network access), so the *default* call is correctly refused. Passing
`require_anchor=False` accepts it anyway, explicitly opting into "I trust the
signer's own word about when this was issued." `render_covenant_demo.py`
(Track 2) takes real timestamps, so that refusal does not appear there.

**3. SELECTIVE DISCLOSURE.** The three ingredients form a Merkle tree, not a
flat signed list. `prove_ingredient()` reveals exactly one ingredient's
record plus the sibling hashes needed to recompute the root — a party can
confirm "yes, this specific asset was used" without learning what the other
two ingredients were.

**4. THE MONEY SHOT.** A second render swaps in `scraped_v1.safetensors`,
which was **never registered** in the store. The gate cannot resolve a grant
for it, so it refuses at the very first read — `AssetNotRegistered`. No bytes
from the unlicensed file ever reach the renderer, and no partial covenant is
produced.

**5. WRONG TERRITORY.** A different failure mode: the photo *is* registered
and has a real grant, but that grant only covers the US, and this render's
context says the campaign is running in the UK. The bundled
`toy_territory_window_policy` checks the grant's `territories` field against
the context and raises `PolicyDenied` — a licensed asset, refused for *this*
use.

**6. NO COVENANT OVER A DENIED RENDER.** `issue()` itself checks whether the
gate recorded any refusal and raises rather than signing, even if you still
have a rendered file in hand. A covenant can only ever describe a render that
was clean end to end.

**7. ONE BYTE CHANGES.** The master file is copied with its last byte
flipped. `verify()` recomputes the SHA-256 of the delivered bytes; it no
longer matches the digest inside the signed covenant body, so verification
fails with a digest mismatch. The covenant binds *exact bytes*, not "an
asset" in the abstract — re-encoding, cropping, or even one flipped bit
invalidates it.

**The closing block** is printed by the demo itself and is worth reading
verbatim — see [Coverage / honest limits](#coverage--honest-limits) below for
the fuller version.

---

## Track 2 — the real thing

A genuine SDXL render through ComfyUI, gated by the same library, ending in a
signed covenant over a real PNG that a third party can verify offline.

### Prerequisites

1. **ComfyUI itself**, installed and working, with its own virtual environment
   (which already includes `torch`, `numpy`, `pillow`). Install per ComfyUI's
   own instructions: <https://github.com/comfyanonymous/ComfyUI>. This repo
   does not install ComfyUI for you.
2. **`cryptography` inside ComfyUI's own venv** — `render_covenant_demo.py`
   runs under *that* interpreter, not Track 1's. `smoke_covenant`'s only hard
   dependency has to be present there too:
   ```
   "$COMFYUI_ROOT/.venv/Scripts/python.exe" -m pip install cryptography
   ```
   (ComfyUI does not install `cryptography` for its own purposes; check with
   `doctor.py` rather than assuming either way.)
3. **One SDXL base checkpoint and two specific LoRAs** — see below for exactly
   why these two and not any two.
4. **`COMFYUI_ROOT`**, only if ComfyUI is not already found automatically.
   `covenant/_paths.py` auto-detects a checkout at `../ComfyUI` /
   `../../ComfyUI` relative to this repo, or `~/ComfyUI` / `~/comfyui`; if
   yours lives somewhere else:
   ```
   # Git Bash
   export COMFYUI_ROOT="D:/apps/ComfyUI"
   # PowerShell
   $env:COMFYUI_ROOT = "D:/apps/ComfyUI"
   ```

Run `python doctor.py` after installing cryptography into ComfyUI's
venv — it checks all of the above (plus the two model files, network
reachability of the timestamp authorities, and free disk space) and prints
the exact command to run next.

### The checkpoint and the two LoRAs, and why this exact pair

| file | source | size | licence | commercial use |
|---|---|---|---|---|
| `sd_xl_base_1.0.safetensors` | [stabilityai/stable-diffusion-xl-base-1.0](https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0) | ~6.5 GiB (6.94 GB) | CreativeML OpenRAIL++-M | permitted, with use restrictions |
| `dmd2_sdxl_4step_lora_fp16.safetensors` | [tianweiy/DMD2](https://huggingface.co/tianweiy/DMD2) | ~375 MiB (394 MB) | CC-BY-NC-4.0 | **prohibited** |
| `pixel-art-xl.safetensors` | [nerijs/pixel-art-xl](https://huggingface.co/nerijs/pixel-art-xl) | ~163 MiB (170.5 MB) | CreativeML OpenRAIL-M | permitted, with use restrictions |

Sizes and licence tags above are as published on Hugging Face at the time of
writing (checked 2026-07-29) — confirm at download time; a repo can change.
The licence readings themselves are `smoke_covenant.policies.LICENCE_TERMS`'s
transcriptions, and that module says plainly that a human transcribed them
and they should be reviewed before real use — this is not legal advice.

Place the checkpoint in `$COMFYUI_ROOT/models/checkpoints/` and both LoRAs in
`$COMFYUI_ROOT/models/loras/`.

**Why this exact pair, and not any two LoRAs:** DMD2 is a *speed* LoRA — it
turns a normal 20–40 step SDXL render into 4 steps, which is exactly the kind
of asset a production team would reach for first to cut render cost or
iterate faster. It is licensed **CC-BY-NC-4.0: non-commercial only.**
`pixel-art-xl` is a completely ordinary style LoRA under OpenRAIL-M, which
*does* permit commercial use. Put them side by side in a paid-advertising
context and the asset everyone wants to reach for is the one the licence
forbids using there — a real, easy-to-miss trap, not a contrived one. That
is the entire point of registering *real* licences here instead of another
toy example: `smoke_covenant/policies.py` documents that the bundled toy
policy's vocabulary (territory, expiry, channel) genuinely cannot express the
one fact that separates these three files — `commercial_use` — and had to be
extended once real licences were registered against it.

### The three cases

```
"$COMFYUI_ROOT/.venv/Scripts/python.exe" render_covenant_demo.py --lora dmd2
"$COMFYUI_ROOT/.venv/Scripts/python.exe" render_covenant_demo.py --lora pixel-art
"$COMFYUI_ROOT/.venv/Scripts/python.exe" render_covenant_demo.py --lora dmd2 --non-commercial
```

- **Case A** (`--lora dmd2`) — a paid advertisement using the CC-BY-NC LoRA.
  **Expected: BLOCKED.** The gate refuses the moment `LoraLoader` tries to
  resolve the file, before sampling ever starts. Real, verified output from
  this exact command against a real checkpoint and LoRA on this machine:
  ```
  checkpoint: sd_xl_base_1.0.safetensors
  lora:       dmd2_sdxl_4step_lora_fp16.safetensors
  purpose:    COMMERCIAL (paid advertisement)
    sd_xl_base_1.0.safetensors                   CreativeML OpenRAIL++-M    commercial=permitted
    dmd2_sdxl_4step_lora_fp16.safetensors        CC-BY-NC-4.0               commercial=prohibited

  ==========================================================================
  RENDERING THROUGH THE GATE
  ==========================================================================

    [BLOCKED] blocked by Render Covenant gate: dmd2_sdxl_4step_lora_fp16.safetensors (lora)
    policy media-licence-v0 refused grant licence:CC-BY-NC-4.0 (model_licence) for asset b3d9173815a4b595...

    The render stopped. No master was produced, so there is nothing
    to covenant -- which is the entire point: the gate refuses at the
    moment of use, not in a report afterwards.
  ```
- **Case B** (`--lora pixel-art`) — the same paid-advertisement context, a
  LoRA whose licence permits commercial use. **Expected: a real render**,
  through the gate, ending in a signed covenant and an offline verification,
  exactly like Track 1's steps 1–3 but over genuine SDXL output.
- **Case C** (`--lora dmd2 --non-commercial`) — the *same* DMD2 asset as
  Case A, rendered as an internal/non-commercial piece instead of a paid ad.
  **Expected: admitted.** Nothing about the file changed; only the render's
  declared *context* did — which is the whole design: a covenant checks a
  policy against a grant **and** the context of this specific use, not just
  "is this file allowed, ever."

Add `--steps 8` to any case for a faster render, and `--no-anchor` to skip
the RFC 3161 timestamp step entirely (useful offline; `verify()` must then be
told `require_anchor=False`, same as Track 1). Without `--no-anchor`, issuing
a covenant reaches out to two public timestamp authorities (DigiCert and
Sigstore) over the network; `doctor.py` checks both are reachable.

### Going further

- The production integration path is a ComfyUI **custom node**
  (`SmokeCovenantIssue`), configured by a JSON file rather than CLI flags —
  see `comfy_node/README.md` for the install step (a symlink/junction, not a
  pip install), the full config schema, and its own coverage notes for
  server-mode ComfyUI specifically (output-cache blind spots, thread
  affinity, and more).
- `render_covenant_demo.py --stage` copies admitted assets into gate-owned,
  content-addressed staging before the loader opens them — the only fully
  TOCTOU-free path (`smoke_covenant/gate.py`'s `admit_staged` docstring
  explains exactly what this closes that holding an open file descriptor does
  not).

---

## Coverage / honest limits

Read this before describing what a Render Covenant proves to anyone else —
these limits are not an afterthought, they are part of what the claim
actually is.

**What a covenant asserts, stated as narrowly as it is true** (from
`smoke_covenant/covenant.py`): *this organization ran policy P over the
ingredients its render actually read through a hermetic gate, got result R,
and bound R to exactly these master bytes at a time it can prove it did not
choose afterwards.*

**What it never claims** (from `smoke_covenant/grants.py`'s non-goals — carry
these with any external description of this format):
- does not prove an undisclosed asset was not copied in out of band
- does not prove a generator's training set was clean
- does not prove a grant signer told the truth, or held authority to sign
- does not recognise a cropped, re-encoded, or perceptually similar copy
- does not prove anything about bytes that never crossed the gate

**The policy is not a rights engine.** `toy_territory_window_policy` is a
labelled demo stub (territory/expiry/channel only). `media_licence_policy`
adds exactly two more columns — `commercial_use` and `use_restrictions` —
because those are the two real licences in this repo actually needed, and
stops there: no sublicensing, derivative-works, attribution, moral-rights,
likeness, or per-channel-window semantics. `smoke_covenant/policies.py` is
explicit that it does not *interpret* a licence — a human read the licence
text and wrote the terms into a `Grant`; the policy only re-applies that
human's reading, consistently, at the moment of use. The intended production
shape is `ExternalRightsPolicy`, which delegates the actual decision to a
real rights system (a DAM, Rightsline, a human-in-the-loop queue) and treats
any resolver exception as a refusal.

**An escape invalidates the claim, it does not weaken it.** `HermeticGate` in
strict mode (the default) raises the instant something reads bytes without
going through it. This is a deliberate design choice explained in
`smoke_covenant/gate.py`: a covenant that quietly tolerated an unmediated read
would be strictly worse than no covenant, because it would look
authoritative while proving nothing about those bytes.

**The ComfyUI adapter's coverage boundary** (from
`smoke_covenant/adapters/comfy.py`'s own module docstring — read there for
the full reasoning): it patches three real choke points ComfyUI actually
resolves assets through — `folder_paths.get_full_path` (checkpoints, LoRAs,
VAEs, ControlNets, CLIP, UNets, upscalers…), `folder_paths.get_annotated_filepath`
(`LoadImage`), and `comfy.sd1_clip.load_embed` (textual-inversion embeddings,
which reach the loader as a directory list and would otherwise bypass the
gate entirely). It does **not** cover a custom node that opens an arbitrary
path with `builtins.open`, downloads a URL at runtime, or reads from a
database — those escape the gate, and `audit_escapes=True` is a *diagnostic*
that can catch honest mistakes, not a guarantee against a node built to evade
it. Running the adapter inside ComfyUI's server (via `comfy_node/`) adds
further, separately-documented gaps — output-cache blind spots, thread
affinity, prompt-scoped rather than per-image-lineage-scoped ingredients —
see the **Coverage** section of `comfy_node/README.md` for the complete,
numbered list; it is not duplicated here so it cannot drift out of sync with
this file.

**C2PA integration (`smoke_covenant/c2pa.py`), if you use it:** riding inside
a C2PA manifest does not make the covenant's claim true, does not sign under
a C2PA-trusted identity (this module emits an *unsigned* manifest
definition), and a C2PA manifest can be stripped from a file entirely by a
re-encode or a tool that drops JUMBF — absence of a covenant proves nothing,
same as absence of Content Credentials proves nothing. C2PA validity and
covenant validity are checked independently; a green result from one says
nothing about the other.

---

## How this was verified

Track 1's exact commands and output above were run against this checkout, in
a brand-new virtual environment created solely for this check (only
`cryptography` installed, nothing pre-existing on `PYTHONPATH`), confirming
the on-ramp genuinely needs nothing beyond `pip install cryptography`. Case A
of Track 2 was run against a real ComfyUI installation with the real
checkpoint and both real LoRAs in place; its output above is genuine. Case
B/C were exercised far enough to confirm the gate correctly admits
`pixel-art-xl` and the render itself completes — see the "Known issue" note
above for where that particular run currently stops.
