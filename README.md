# Render Covenants

Hermetic build provenance for media. **Not a clearance system.**

A render's ingredient list is built from what the renderer **actually read** through a
gate that can refuse — not from a declaration. The one rule everything else serves:

> An asset cannot participate in a render unless its fingerprint resolves to an
> acceptable, authority-signed grant.

A finished covenant binds a policy decision to the **exact delivered bytes**, carries a
**trusted timestamp** so the issue time is not the signer's own word, and is verifiable
by a third party **offline** — no contact with the studio.

## What it proves — stated as narrowly as it is true

The organization ran policy *P* over the ingredients its render actually read through a
hermetic gate, got result *R*, and bound *R* to exactly these master bytes at a time it
can prove it did not choose afterwards.

## What it does NOT prove

- that an asset was lawfully obtained
- that a grant signer told the truth, or held authority to sign
- that a generator's training set was clean
- that a cropped or re-encoded copy would be recognised (identity is exact-hash)
- anything about bytes that never crossed the gate

It does not **interpret** a licence. A human reads the licence and writes the terms
into a `Grant`; the gate re-applies that reading consistently at the moment of use and
commits to the result. *Ask a lawyer what a licence means; ask this whether the answer
was applied to the bytes that shipped.*

## Layout

| file | role |
|---|---|
| `smoke_covenant/grants.py` | content-addressed store, `Grant`, policy as a pluggable predicate |
| `smoke_covenant/gate.py` | the hermetic gate — hash → resolve → evaluate → record, or refuse |
| `smoke_covenant/covenant.py` | issue / verify / selective-disclosure / RFC 3161 anchoring |
| `smoke_covenant/policies.py` | commercial-use + use-restriction policy over real model licences |
| `smoke_covenant/adapters/comfy.py` | routes ComfyUI's asset loads through the gate |

## Status: private, and standalone

The package runs on its own. Only third-party dependency: **`cryptography`**.

`python test_standalone.py` proves it — it refuses to pass if `smoke_trust` is
importable at all, then exercises the whole path (gate → issue → verify → selective
disclosure → tamper → C2PA round trip) to show the result is real rather than an
import-graph argument.

The four primitives it used to borrow are vendored under `smoke_covenant/_vendor/`:

| vendored | how |
|---|---|
| `asn1.py`, `rfc3161.py` | copied wholesale — self-contained, stdlib only |
| `canonical_json_bytes` | extracted; large source module, unrelated deps |
| `merkle_root_v0` / `_proof_` / `verify_inclusion_` | extracted, same reason |

A hand-extracted canonicalizer or Merkle root that differs from its original by one
byte does not error — it quietly emits covenants nobody else can verify. So
`test_vendor_conformance.py` asserts byte-for-byte agreement against the originals
(500 randomized values, every leaf count 1–33 including the odd-promote path, every
proof index, plus cross-verification where each implementation must accept the
other's proofs). That test only runs **in-tree**, where both copies are present.

## Running it

| | |
|---|---|
| `python test_standalone.py` | no dependencies beyond `cryptography` |
| `python demo_covenant.py` | full chain against stand-in assets |
| `python test_c2pa.py` | C2PA emission and round trip |
| `python test_anchor.py --live` | real RFC 3161 timestamps (network) |

The ComfyUI pieces (`smoke_covenant/adapters/comfy.py`, `comfy_node/`,
`render_covenant_demo.py`, and their tests) additionally need a ComfyUI checkout and
its virtualenv. See `comfy_node/README.md`.

## Coverage boundary (honest)

The ComfyUI adapter covers models, UI-supplied images, and textual-inversion embeddings
— every asset class ComfyUI resolves through its own machinery. A custom node that calls
`open()` on an arbitrary path, fetches a URL at runtime, or reads a database escapes the
gate. Per the theorem, an escape **invalidates** the hermetic claim rather than weakening
it; `audit_escapes=True` is a diagnostic that surfaces weight-shaped escapes, not a
guarantee.
