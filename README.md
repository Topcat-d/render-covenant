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

## Status: private, and not yet standalone

This is an **extraction in progress**. The package currently imports a small number of
pure primitives from the parent `smoke-suite` trust layer, which is **not** in this repo:

- `canonical_json_bytes` (canonical serialization)
- `merkle_root_v0` / `merkle_proof_v0` / `verify_inclusion_v0`
- `message_imprint_digest` / `verify_timestamp_token` (RFC 3161)
- `TSAClient`, `SoftwareMeasurementSigner` (demos only)

Until those are vendored in, the tests and demos run only with `smoke-suite` on the
Python path. Vendoring them — byte-for-byte, with a conformance check against the
originals — is the work that makes this repo self-contained and is deferred to the
open-source pass.

## Coverage boundary (honest)

The ComfyUI adapter covers models, UI-supplied images, and textual-inversion embeddings
— every asset class ComfyUI resolves through its own machinery. A custom node that calls
`open()` on an arbitrary path, fetches a URL at runtime, or reads a database escapes the
gate. Per the theorem, an escape **invalidates** the hermetic claim rather than weakening
it; `audit_escapes=True` is a diagnostic that surfaces weight-shaped escapes, not a
guarantee.
