# Render Covenant

**A Render Covenant is a signed, offline-verifiable statement that a policy
was checked against the licences of the assets a render *actually read*, at
the moment it read them, and that the result is bound to the exact bytes the
render produced.** It is a hermetic build applied to media provenance: the
gate refuses at the moment of use, the same way a hermetic software build
refuses to secretly import an undeclared dependency.

It is **not** a rights engine and does not decide what is legally allowed. It
does not prove an asset was lawfully obtained, that a licensor told the
truth, that a generator's training data was clean, or that a re-encoded copy
would be recognised. It records that a policy you configured was applied,
consistently, to what a render actually loaded — and makes that record
tamper-evident and independently checkable. See `smoke_covenant/grants.py`'s
non-goals and `QUICKSTART.md`'s Coverage / honest limits section for the full
and precise statement of what is and is not claimed; both should travel with
any external description of this format.

## Install

The library (`smoke_covenant`) needs only `cryptography`:

```
pip install -e .
```

or, without an editable install, `pip install -r requirements.txt`. The
ComfyUI-side scripts in this directory need a few more packages, from
ComfyUI's own environment — see `requirements.txt`'s ComfyUI section or the
`comfy` extra (`pip install -e .[comfy]`).

## Licence

Apache License 2.0 — see [`LICENSE`](LICENSE) for the full text and
[`NOTICE`](NOTICE) for vendored-code provenance and third-party attributions.
`smoke_covenant/adapters/comfy.py` and `comfy_node/` hook GPL-3.0 ComfyUI's
public extension points at runtime and vendor none of it; see `NOTICE` for
why that keeps this project's own code under Apache-2.0.

## Start here

**[QUICKSTART.md](QUICKSTART.md)** — a 60-second, dependency-light demo with
no ComfyUI, followed by a real ComfyUI render through the gate, both with
real output and a line-by-line explanation of what each step proves.

For the ComfyUI custom node specifically (production integration, config
format, server-mode coverage notes), see `comfy_node/README.md`.
