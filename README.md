# Render Covenant

**Cryptographic provenance for AI-generated images — a ComfyUI custom node and
Python library that binds every Stable Diffusion / SDXL render to the licensed
assets it actually loaded, and refuses to render when a licence does not
permit it.**

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

A Render Covenant is a signed, offline-verifiable statement that a policy was
checked against the licences of the assets a render *actually read*, at the
moment it read them, and that the result is bound to the exact bytes the render
produced. It is a hermetic build applied to media provenance: the gate refuses
at the moment of use, the same way a hermetic software build refuses to
secretly import an undeclared dependency.

**The refusal is the product.** A checkpoint, LoRA, embedding or input image
that does not resolve to an acceptable grant stops the render. It is not
logged and rendered anyway.

## What it does

- **Licence enforcement at the moment of use.** Model licences (CreativeML
  OpenRAIL++-M, CC-BY-NC, custom terms), stock-asset licences, and sync
  licences are checked against the render's actual context — territory,
  channel, commercial use, release window — when the file is opened, not
  declared in a spreadsheet afterwards.
- **Tamper-evident content provenance.** A P-256 ECDSA signature over a
  canonical body binding the master image digest to a Merkle root of every
  ingredient. One changed byte and the covenant no longer applies.
- **Trusted timestamping (RFC 3161).** Optional anchoring against independent
  timestamp authorities (DigiCert, Sigstore). Unanchored covenants fail closed
  by default, because an unanchored issue time is only the signer's own word.
- **Selective disclosure.** Prove one licensed ingredient was an input while
  keeping the rest of the ingredient list sealed.
- **C2PA emission.** Export the decision as a C2PA-shaped assertion for
  content-credentials pipelines.
- **Offline verification.** A distributor verifies with the covenant JSON, the
  delivered file, and a public key. No network, no contact with the issuer.

## Use cases

Advertising and brand compliance where a campaign must prove which licensed
assets a generative render used; studio and agency audit trails for AI image
generation; model-licence compliance for SDXL checkpoints and LoRAs; rights
management and content authenticity for synthetic media; evidence for
downstream distributors who cannot take the issuer's word for it.

## What it is *not*

It is **not** a rights engine and does not decide what is legally allowed. It
does not prove an asset was lawfully obtained, that a licensor told the truth
or held authority, that a generator's training data was clean, or that a
cropped or re-encoded copy would be recognised. It records that a policy you
configured was applied, consistently, to what a render actually loaded — and
makes that record tamper-evident and independently checkable.

An escape *invalidates* the hermetic claim rather than weakening it: a read
that bypasses the gate is outside the theorem entirely. See
`smoke_covenant/grants.py`'s non-goals, `comfy_node/README.md`'s coverage
section, and `QUICKSTART.md`'s honest-limits section for the full and precise
statement of what is and is not claimed. All three should travel with any
external description of this format.

## Install

The library (`smoke_covenant`) needs only `cryptography`:

```sh
pip install -e .
```

or, without an editable install, `pip install -r requirements.txt`. The
ComfyUI-side scripts need a few more packages, from ComfyUI's own environment —
see `requirements.txt`'s ComfyUI section or the `comfy` extra
(`pip install -e .[comfy]`).

Not sure what you have? The preflight check is stdlib-only, so it runs before
you install anything and tells you exactly what is missing:

```sh
python doctor.py
```

## Start here

**[QUICKSTART.md](QUICKSTART.md)** — a 60-second demo needing no ComfyUI, no
model weights and no GPU, followed by a real ComfyUI diffusion render through
the gate. Both with real output and a line-by-line explanation of what each
step proves.

For the ComfyUI custom node specifically — production integration, config
format, and the server-mode coverage notes — see
[`comfy_node/README.md`](comfy_node/README.md).

## Licence

Apache License 2.0 — see [`LICENSE`](LICENSE) for the full text and
[`NOTICE`](NOTICE) for vendored-code provenance and third-party attributions.
`smoke_covenant/adapters/comfy.py` and `comfy_node/` hook GPL-3.0 ComfyUI's
public extension points at runtime and vendor none of it; see `NOTICE` for why
that keeps this project's own code under Apache-2.0.

---

<sub>Topics: AI image provenance · generative AI licensing compliance · ComfyUI
custom node · Stable Diffusion / SDXL · content authenticity · C2PA content
credentials · tamper-evident audit trail · RFC 3161 trusted timestamping ·
digital signatures · media supply chain · synthetic media rights management</sub>
