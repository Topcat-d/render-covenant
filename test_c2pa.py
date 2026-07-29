"""Does a covenant survive a trip through C2PA's container and still fail closed?

The bet in `c2pa.py` is that Adobe's ecosystem becomes a DISTRIBUTION CHANNEL
rather than a competitor: the covenant rides inside somebody else's manifest and
every C2PA-aware tool carries it. That bet is only worth anything if the covenant
that comes back OUT is the same covenant that went in, and if every way of
editing it in transit is caught.

So this proves three things and refuses to claim a fourth:
  1. round trip -- covenant -> assertion -> JSON -> covenant, and it verifies;
  2. tamper -- editing the master digest anywhere in the assertion breaks it,
     whether or not the flat summary is edited to match;
  3. the anchor survives the trip byte-for-byte and stays bound.

NOT proved here, deliberately: anything about C2PA itself. No claim signature is
checked, no hard binding, no certificate. `verify_manifest` verifies the covenant
and nothing else, and a test that implied otherwise would be the exact
overclaim the module docstring warns about.

The anchor here is a STUB witness -- offline, no token. Real TSA verification is
test_anchor.py's job; this test only proves the anchor BLOCK survives the
container unchanged and stays bound to the signature.

Run:
  python covenant/test_c2pa.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _paths import bootstrap_suite  # noqa: E402

# The signer used to build the covenant under test is the vendored DemoSigner
# (smoke_covenant/_vendor/signer.py), so this script needs no smoke-suite
# checkout.
bootstrap_suite(need_suite=False)

from smoke_covenant._vendor.signer import DemoSigner  # noqa: E402

from smoke_covenant import (  # noqa: E402
    AssetStore, CovenantInvalid, Grant, HermeticGate, issue,
)
from smoke_covenant.c2pa import (  # noqa: E402
    COVENANT_ASSERTION_LABEL,
    covenant_assertion,
    covenant_from_manifest,
    covenant_manifest,
    ingredient_entries,
    verify_manifest,
)
from smoke_covenant.policies import LICENCE_TERMS, media_licence_policy  # noqa: E402

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"\n         {detail}" if detail else ""))
    if not ok:
        failures.append(name)


class _OfflineTSA:
    """Witness SHAPE without a network. Status is 'error' and says so.

    A stub that returned status='ok' with a made-up token would be a lie the
    verifier could not distinguish from a real one, so it returns the honest
    refusal shape instead. That still produces a full anchor block -- which is
    the thing under test here.
    """

    url = "stub://offline-tsa"

    def request_witness(self, anchored_hash_hex: str) -> dict:
        return {
            "type": "rfc3161",
            "url": self.url,
            "status": "error",
            "error": "offline test stub -- no token requested",
        }


def build(tmp: Path):
    """A two-ingredient render: enough that the Merkle root is not the leaf."""
    photo = tmp / "photo.jpg"
    photo.write_bytes(b"<photo bytes>")
    ckpt = tmp / "sd_xl_base_1.0.safetensors"
    ckpt.write_bytes(b"<checkpoint bytes>")
    master = tmp / "final-ad.png"
    master.write_bytes(b"PNG:<the delivered master>")

    store = AssetStore()
    store.register(photo, Grant(
        grant_id="licence:CreativeML OpenRAIL-M", asset_digest="", kind="model_licence",
        terms=LICENCE_TERMS["creativeml-openrail-m"],
        signer_spki="huggingface:model-author"), label="photo.jpg")
    store.register(ckpt, Grant(
        grant_id="licence:CreativeML OpenRAIL++-M", asset_digest="", kind="model_licence",
        terms=LICENCE_TERMS["creativeml-openrail++-m"],
        signer_spki="huggingface:stabilityai"), label="sd_xl_base_1.0.safetensors")

    ctx = {"territory": "US", "channels": ["paid-social"], "release_end": "2027-02-01",
           "commercial": True, "intended_uses": ["advertising"]}
    gate = HermeticGate(store, media_licence_policy(), ctx)
    gate.admit(photo, "photograph")
    gate.admit(ckpt, "checkpoint")

    signer = DemoSigner()
    cov, ings = issue(gate, str(master), signer=signer,
                      renderer_identity={"engine": "ComfyUI", "sampler": "euler"},
                      tsa_clients=[_OfflineTSA()])
    return cov, ings, master, signer


def wire(manifest: dict) -> dict:
    """Through real JSON, because that is what a C2PA tool actually transports."""
    return json.loads(json.dumps(manifest))


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="covenant-c2pa-"))
    cov, ings, master, signer = build(tmp)
    pub = signer.public_key()

    print("=" * 74)
    print("Covenant as a C2PA custom assertion")
    print("=" * 74)

    manifest = covenant_manifest(
        cov, ingredients=ings, title=master.name, format="image/png",
        claim_generator="smoke-covenant/0",
    )
    on_the_wire = wire(manifest)

    # --- 1. round trip ------------------------------------------------------
    got = covenant_from_manifest(on_the_wire)
    check("covenant survives JSON round trip byte-identically",
          got.to_dict() == cov.to_dict())

    # require_anchor=False: the stub witness carries no token, so trusted time
    # cannot be established offline. test_anchor.py owns that property.
    back, report = verify_manifest(on_the_wire, str(master), pub, require_anchor=False)
    check("extracted covenant VERIFIES against the delivered master",
          back.master_digest == cov.master_digest and report["anchored"] is True,
          f"master {back.master_digest[:16]}...  anchored={report['anchored']} "
          f"witnesses_verified={report['verified']} (stub TSA: no token)")

    label = on_the_wire["assertions"][0]["label"]
    check("assertion uses a reverse-DNS custom label",
          label == COVENANT_ASSERTION_LABEL and label.count(".") >= 2,
          label)
    check("manifest definition carries the documented top-level keys",
          {"claim_generator", "claim_generator_info", "assertions"}
          <= set(on_the_wire),
          f"keys: {sorted(on_the_wire)}")
    check("manifest carries NO signing config (no borrowed C2PA identity)",
          not ({"alg", "private_key", "sign_cert", "ta_url"} & set(on_the_wire)),
          "an unsigned manifest is visibly unsigned; one signed by a test cert is not")

    # --- 2. the anchor travels ----------------------------------------------
    data = on_the_wire["assertions"][0]["data"]
    anchor = data["covenant"]["anchor"]
    check("assertion carries the anchor block",
          anchor is not None and anchor["anchored_hash"] == cov.anchor_id().hex(),
          f"anchored_hash {anchor['anchored_hash'][:16]}... == "
          f"SHA256(domain || signing_digest || r || s)")
    check("anchor witnesses survive the container",
          anchor["witnesses"] == (cov.anchor or {})["witnesses"],
          f"{len(anchor['witnesses'])} witness(es), status="
          f"{anchor['witnesses'][0]['status']}")
    check("summary mirrors the anchor for viewers that do not walk the body",
          data["summary"]["anchored"] is True
          and data["summary"]["anchored_hash"] == anchor["anchored_hash"])

    # Edit the mirror to match, so the summary cross-check CANNOT be what
    # catches this. What is left is the anchor's own binding to the signature.
    m = wire(manifest)
    m["assertions"][0]["data"]["covenant"]["anchor"]["anchored_hash"] = "ff" * 32
    m["assertions"][0]["data"]["summary"]["anchored_hash"] = "ff" * 32
    broke = False
    try:
        verify_manifest(m, str(master), pub, require_anchor=False)
    except CovenantInvalid as exc:
        broke, msg = True, str(exc)
    check("re-pointing the anchor at another covenant is REFUSED", broke,
          msg if broke else "a swapped anchor was accepted")
    check("...caught by the anchor's binding to this signature, not the mirror",
          broke and "anchor is bound to a different covenant" in msg,
          msg if broke else "")

    # --- 3. tampering with the master digest --------------------------------
    # (a) edit BOTH copies so they agree -- the summary cross-check cannot help,
    #     and only the signature over the canonical body catches it. This is the
    #     one that matters: it proves the crypto is load-bearing, not the mirror.
    m = wire(manifest)
    fake = "de" * 32
    m["assertions"][0]["data"]["covenant"]["body"]["master"]["digest"] = fake
    m["assertions"][0]["data"]["summary"]["master_digest"] = fake
    broke = False
    try:
        verify_manifest(m, str(master), pub, require_anchor=False)
    except CovenantInvalid as exc:
        broke, msg = True, str(exc)
    check("editing the master digest in BOTH body and summary is REFUSED", broke,
          msg if broke else "a rewritten covenant verified -- BUG")
    check("...and it is the SIGNATURE that catches it, not the mirror",
          broke and "signature" in msg.lower(), msg if broke else "")

    # (b) edit only the flat summary -- a viewer would show the wrong digest
    #     while the verifier read the right one. Refused, not resolved.
    m = wire(manifest)
    m["assertions"][0]["data"]["summary"]["master_digest"] = fake
    broke = False
    try:
        covenant_from_manifest(m)
    except CovenantInvalid as exc:
        broke, msg = True, str(exc)
    check("editing ONLY the viewer-facing summary is REFUSED", broke,
          msg if broke else "the summary and the body were allowed to disagree")

    # (c) drop the summary entirely and edit only the signed body -- proves the
    #     cross-check is a convenience and the signature is the real defence.
    m = wire(manifest)
    del m["assertions"][0]["data"]["summary"]
    m["assertions"][0]["data"]["covenant"]["body"]["master"]["digest"] = fake
    broke = False
    try:
        verify_manifest(m, str(master), pub, require_anchor=False)
    except CovenantInvalid as exc:
        broke, msg = True, str(exc)
    check("with no summary at all, the signed body still catches the edit", broke,
          msg if broke else "")

    # (d) the bytes themselves change. The covenant is bound to the master, not
    #     to the manifest it happens to be travelling in.
    tampered = master.with_name("tampered.png")
    b = bytearray(master.read_bytes())
    b[-1] ^= 0x01
    tampered.write_bytes(bytes(b))
    broke = False
    try:
        verify_manifest(on_the_wire, str(tampered), pub, require_anchor=False)
    except CovenantInvalid as exc:
        broke, msg = True, str(exc).split("--")[0].strip()
    check("one flipped byte in the master is REFUSED", broke, msg if broke else "")

    # --- 4. ingredient mapping ----------------------------------------------
    entries = ingredient_entries(ings)
    check("every ingredient maps to a C2PA ingredient entry",
          len(entries) == len(ings) == 2, f"{len(entries)} entries")
    check("relationship is the spec's inputTo",
          all(e["relationship"] == "inputTo" for e in entries),
          "parentOf/componentOf/inputTo are the only three; inputTo is the honest one")
    check("titles carry through so a viewer shows something recognisable",
          {e["title"] for e in entries}
          == {"photo.jpg", "sd_xl_base_1.0.safetensors"},
          str(sorted(e["title"] for e in entries)))
    flat = json.dumps(entries)
    check("rights facts are NOT smuggled into ingredient entries",
          "grant_id" not in flat and "signer_spki" not in flat
          and "huggingface" not in flat and "licence:" not in flat,
          "grant id / kind / signer / role have no C2PA field and are not forced "
          "into one -- they live only in the custom assertion")
    check("the ingredient digest is still recoverable from the covenant",
          all(any(i.asset_digest in e["instance_id"] for e in entries) for i in ings),
          "as a clearly-ours URN, not a fabricated xmp:iid")

    # --- 5. other manifest shapes a real tool hands back --------------------
    store_report = {
        "active_manifest": "urn:uuid:test-active",
        "manifests": {"urn:uuid:test-active": wire(manifest)},
    }
    check("extracts from a manifest STORE read report (active_manifest/manifests)",
          covenant_from_manifest(store_report).to_dict() == cov.to_dict())

    label_keyed = {"assertions": {COVENANT_ASSERTION_LABEL:
                                  wire(covenant_assertion(cov))["data"]}}
    check("extracts from the label-keyed JSON-LD serialization",
          covenant_from_manifest(label_keyed).to_dict() == cov.to_dict())

    # --- 6. fail closed on absence and ambiguity ---------------------------
    refused = False
    try:
        covenant_from_manifest({"claim_generator": "someone-else/1", "assertions": []})
    except CovenantInvalid:
        refused = True
    check("a manifest with no covenant is REFUSED, not 'unverified'", refused,
          "absence of a covenant proves nothing -- so it cannot return a pass")

    m = wire(manifest)
    dup = wire(covenant_assertion(cov))
    dup["label"] = COVENANT_ASSERTION_LABEL + "__1"
    m["assertions"].append(dup)
    refused = False
    try:
        covenant_from_manifest(m)
    except CovenantInvalid as exc:
        refused, msg = True, str(exc).split("--")[0].strip()
    check("two covenant assertions in one manifest are AMBIGUOUS and refused",
          refused, msg if refused else "one was silently picked")

    m = wire(manifest)
    m["assertions"][0]["data"]["covenant_version"] = "smoke.covenant.v9"
    refused = False
    try:
        covenant_from_manifest(m)
    except CovenantInvalid:
        refused = True
    check("an unknown covenant version is refused, not read as if it matched", refused)

    print("=" * 74)
    if failures:
        print(f"FAILED: {len(failures)} -> {', '.join(failures)}")
        return 1
    print("The covenant rides C2PA intact. C2PA carries it; it does not bless it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
