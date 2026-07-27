"""RFC 3161 trusted-timestamp structures: request builder, token parser, verifier.

The datum timestamped for a chain anchor is the 64-char lowercase hex string
of the audit chain-head hash, encoded as ASCII. The RFC 3161 messageImprint
is SHA-256 over that datum:

    imprint = SHA-256(ascii(anchored_hash_hex))

All verifiers (Python here, Go smokeverify, Node smoke-verify.js) recompute
this identically — see trust/verifiers/testdata/anchor_vectors.json.

Verification model: PINNED-KEY, consistent with every other Smoke verifier
(pinned admissibility key, pinned root key). The deployer pins the TSA's
signing public key as a DER SubjectPublicKeyInfo (SPKI). We verify:

  1. token parses as CMS SignedData carrying a TSTInfo,
  2. TSTInfo.messageImprint is SHA-256 and matches the expected imprint,
  3. signed attributes are present and bind content-type + message-digest
     (message-digest attr == SHA-256 of the TSTInfo bytes),
  4. the SignerInfo signature over the signed attributes verifies under one
     of the pinned SPKIs (ECDSA-P256/SHA-256 or RSA-PKCS1v15/SHA-256).

We do NOT perform X.509 path validation to a public root and do not claim
eIDAS qualified-timestamp status. Fail closed: any parse or check failure
is a verification failure.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from . import asn1

# OIDs
OID_SHA256 = "2.16.840.1.101.3.4.2.1"
OID_SIGNED_DATA = "1.2.840.113549.1.7.2"
OID_CT_TSTINFO = "1.2.840.113549.1.9.16.1.4"
OID_ATTR_CONTENT_TYPE = "1.2.840.113549.1.9.3"
OID_ATTR_MESSAGE_DIGEST = "1.2.840.113549.1.9.4"
OID_ECDSA_SHA256 = "1.2.840.10045.4.3.2"
OID_RSA_SHA256 = "1.2.840.113549.1.1.11"
OID_RSA_ENCRYPTION = "1.2.840.113549.1.1.1"

_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")


class Rfc3161Error(ValueError):
    """Malformed RFC 3161 structure. Treat as verification failure."""


def message_imprint_digest(anchored_hash_hex: str) -> bytes:
    """SHA-256 imprint over the ASCII bytes of the 64-hex chain-head string."""
    normalized = anchored_hash_hex.strip().lower()
    if not _HEX64_RE.match(normalized):
        raise Rfc3161Error("anchored hash must be 64 lowercase hex chars")
    return hashlib.sha256(normalized.encode("ascii")).digest()


# ---------------------------------------------------------------------------
# TimeStampReq
# ---------------------------------------------------------------------------

def build_timestamp_request(
    imprint_sha256: bytes,
    nonce: Optional[int] = None,
    cert_req: bool = True,
) -> bytes:
    """Build a DER TimeStampReq for a SHA-256 message imprint."""
    if len(imprint_sha256) != 32:
        raise Rfc3161Error("imprint must be a 32-byte SHA-256 digest")
    alg_id = asn1.encode_sequence(asn1.encode_oid(OID_SHA256), asn1.encode_null())
    message_imprint = asn1.encode_sequence(
        alg_id, asn1.encode_octet_string(imprint_sha256)
    )
    parts = [asn1.encode_integer(1), message_imprint]
    if nonce is not None:
        parts.append(asn1.encode_integer(nonce))
    if cert_req:
        parts.append(asn1.encode_boolean(True))
    return asn1.encode_sequence(*parts)


# ---------------------------------------------------------------------------
# TimeStampResp / TimeStampToken parsing
# ---------------------------------------------------------------------------

@dataclass
class TimestampResponse:
    status: int
    status_strings: List[str]
    token_der: Optional[bytes]

    @property
    def granted(self) -> bool:
        return self.status in (0, 1)  # granted / grantedWithMods


@dataclass
class ParsedTimestampToken:
    tstinfo_der: bytes
    policy_oid: str
    imprint_alg_oid: str
    imprint_digest: bytes
    serial: int
    gen_time: str
    nonce: Optional[int]
    signer_digest_alg_oid: str
    signer_sig_alg_oid: str
    signed_attrs_raw: bytes  # original [0] IMPLICIT TLV bytes
    signature: bytes
    certificates_der: List[bytes] = field(default_factory=list)


def parse_timestamp_response(der: bytes) -> TimestampResponse:
    tag, _, content, _ = asn1.read_tlv(der, 0)
    asn1.expect_tag(tag, asn1.TAG_SEQUENCE, "TimeStampResp")
    children = asn1.read_children(content)
    if not children:
        raise Rfc3161Error("empty TimeStampResp")

    status_tag, status_content, _ = children[0]
    asn1.expect_tag(status_tag, asn1.TAG_SEQUENCE, "PKIStatusInfo")
    status_children = asn1.read_children(status_content)
    if not status_children or status_children[0][0] != asn1.TAG_INTEGER:
        raise Rfc3161Error("PKIStatusInfo missing status INTEGER")
    status = asn1.decode_integer(status_children[0][1])

    status_strings: List[str] = []
    for tag2, content2, _ in status_children[1:]:
        if tag2 == asn1.TAG_SEQUENCE:  # PKIFreeText: SEQUENCE OF UTF8String
            for tag3, content3, _ in asn1.read_children(content2):
                if tag3 == asn1.TAG_UTF8_STRING:
                    status_strings.append(content3.decode("utf-8", "replace"))

    token_der: Optional[bytes] = None
    if len(children) > 1:
        token_der = children[1][2]  # full ContentInfo TLV
    return TimestampResponse(status=status, status_strings=status_strings,
                             token_der=token_der)


def _parse_tstinfo(tstinfo_der: bytes):
    tag, _, content, _ = asn1.read_tlv(tstinfo_der, 0)
    asn1.expect_tag(tag, asn1.TAG_SEQUENCE, "TSTInfo")
    children = asn1.read_children(content)
    if len(children) < 5:
        raise Rfc3161Error("TSTInfo too short")

    version = asn1.decode_integer(children[0][1])
    if version != 1:
        raise Rfc3161Error(f"unsupported TSTInfo version {version}")
    asn1.expect_tag(children[1][0], asn1.TAG_OID, "TSTInfo.policy")
    policy_oid = asn1.decode_oid(children[1][1])

    asn1.expect_tag(children[2][0], asn1.TAG_SEQUENCE, "TSTInfo.messageImprint")
    imprint_children = asn1.read_children(children[2][1])
    if len(imprint_children) != 2:
        raise Rfc3161Error("malformed messageImprint")
    alg_children = asn1.read_children(imprint_children[0][1])
    if not alg_children or alg_children[0][0] != asn1.TAG_OID:
        raise Rfc3161Error("malformed messageImprint algorithm")
    imprint_alg_oid = asn1.decode_oid(alg_children[0][1])
    asn1.expect_tag(imprint_children[1][0], asn1.TAG_OCTET_STRING,
                    "messageImprint.hashedMessage")
    imprint_digest = imprint_children[1][1]

    asn1.expect_tag(children[3][0], asn1.TAG_INTEGER, "TSTInfo.serialNumber")
    serial = asn1.decode_integer(children[3][1])
    asn1.expect_tag(children[4][0], asn1.TAG_GENERALIZED_TIME, "TSTInfo.genTime")
    gen_time = children[4][1].decode("ascii", "replace")

    nonce: Optional[int] = None
    # Optional trailing fields: accuracy SEQUENCE, ordering BOOLEAN,
    # nonce INTEGER, tsa [0], extensions [1]. Walk by tag.
    for tag2, content2, _ in children[5:]:
        if tag2 == asn1.TAG_INTEGER:
            nonce = asn1.decode_integer(content2)
    return policy_oid, imprint_alg_oid, imprint_digest, serial, gen_time, nonce


def parse_timestamp_token(token_der: bytes) -> ParsedTimestampToken:
    """Parse a TimeStampToken (CMS ContentInfo/SignedData wrapping TSTInfo)."""
    tag, _, content, _ = asn1.read_tlv(token_der, 0)
    asn1.expect_tag(tag, asn1.TAG_SEQUENCE, "ContentInfo")
    children = asn1.read_children(content)
    if len(children) != 2 or children[0][0] != asn1.TAG_OID:
        raise Rfc3161Error("malformed ContentInfo")
    if asn1.decode_oid(children[0][1]) != OID_SIGNED_DATA:
        raise Rfc3161Error("ContentInfo is not SignedData")
    if children[1][0] != 0xA0:
        raise Rfc3161Error("missing SignedData [0] wrapper")

    sd_children = asn1.read_children(children[1][1])
    if len(sd_children) != 1 or sd_children[0][0] != asn1.TAG_SEQUENCE:
        raise Rfc3161Error("malformed SignedData wrapper")
    fields = asn1.read_children(sd_children[0][1])
    if len(fields) < 4:
        raise Rfc3161Error("SignedData too short")

    idx = 0
    asn1.expect_tag(fields[idx][0], asn1.TAG_INTEGER, "SignedData.version")
    idx += 1
    asn1.expect_tag(fields[idx][0], asn1.TAG_SET, "SignedData.digestAlgorithms")
    idx += 1

    # encapContentInfo
    asn1.expect_tag(fields[idx][0], asn1.TAG_SEQUENCE, "encapContentInfo")
    eci_children = asn1.read_children(fields[idx][1])
    idx += 1
    if not eci_children or eci_children[0][0] != asn1.TAG_OID:
        raise Rfc3161Error("malformed encapContentInfo")
    if asn1.decode_oid(eci_children[0][1]) != OID_CT_TSTINFO:
        raise Rfc3161Error("eContentType is not id-ct-TSTInfo")
    if len(eci_children) < 2 or eci_children[1][0] != 0xA0:
        raise Rfc3161Error("missing eContent")
    econtent_children = asn1.read_children(eci_children[1][1])
    if len(econtent_children) != 1 or econtent_children[0][0] != asn1.TAG_OCTET_STRING:
        raise Rfc3161Error("malformed eContent OCTET STRING")
    tstinfo_der = econtent_children[0][1]

    # Optional certificates [0] IMPLICIT, crls [1] IMPLICIT
    certificates_der: List[bytes] = []
    while idx < len(fields) and fields[idx][0] in (0xA0, 0xA1):
        if fields[idx][0] == 0xA0:
            for _, _, full in asn1.read_children(fields[idx][1]):
                certificates_der.append(full)
        idx += 1

    if idx >= len(fields) or fields[idx][0] != asn1.TAG_SET:
        raise Rfc3161Error("missing signerInfos")
    signer_infos = asn1.read_children(fields[idx][1])
    if len(signer_infos) != 1:
        raise Rfc3161Error(f"expected exactly 1 SignerInfo, got {len(signer_infos)}")
    si_children = asn1.read_children(signer_infos[0][1])
    # SIX mandatory fields when signed attributes are present, which RFC 3161
    # requires: version, sid, digestAlgorithm, [0] signedAttrs, signatureAlgorithm,
    # signature. The walk below starts at index 2 and steps three times, so it
    # reads si_children[5]. A `< 5` guard here let a 5-field SignerInfo through
    # and then raised IndexError -- which is neither DerError nor Rfc3161Error,
    # so it escaped every caller's except clause and crashed the verifier instead
    # of returning a structured denial.
    if len(si_children) < 6:
        raise Rfc3161Error(
            f"SignerInfo too short: {len(si_children)} fields, need 6 "
            "(version, sid, digestAlgorithm, signedAttrs, signatureAlgorithm, signature)"
        )

    # version, sid, digestAlgorithm, [0] signedAttrs, signatureAlgorithm, signature
    si_idx = 2  # skip version + sid
    asn1.expect_tag(si_children[si_idx][0], asn1.TAG_SEQUENCE,
                    "SignerInfo.digestAlgorithm")
    dig_alg_children = asn1.read_children(si_children[si_idx][1])
    if not dig_alg_children or dig_alg_children[0][0] != asn1.TAG_OID:
        raise Rfc3161Error("malformed digestAlgorithm")
    signer_digest_alg_oid = asn1.decode_oid(dig_alg_children[0][1])
    si_idx += 1

    if si_children[si_idx][0] != 0xA0:
        # RFC 3161 REQUIRES signed attributes on a timestamp token.
        raise Rfc3161Error("SignerInfo has no signed attributes")
    signed_attrs_raw = si_children[si_idx][2]
    si_idx += 1

    asn1.expect_tag(si_children[si_idx][0], asn1.TAG_SEQUENCE,
                    "SignerInfo.signatureAlgorithm")
    sig_alg_children = asn1.read_children(si_children[si_idx][1])
    if not sig_alg_children or sig_alg_children[0][0] != asn1.TAG_OID:
        raise Rfc3161Error("malformed signatureAlgorithm")
    signer_sig_alg_oid = asn1.decode_oid(sig_alg_children[0][1])
    si_idx += 1

    asn1.expect_tag(si_children[si_idx][0], asn1.TAG_OCTET_STRING,
                    "SignerInfo.signature")
    signature = si_children[si_idx][1]

    policy_oid, imprint_alg_oid, imprint_digest, serial, gen_time, nonce = (
        _parse_tstinfo(tstinfo_der)
    )
    return ParsedTimestampToken(
        tstinfo_der=tstinfo_der,
        policy_oid=policy_oid,
        imprint_alg_oid=imprint_alg_oid,
        imprint_digest=imprint_digest,
        serial=serial,
        gen_time=gen_time,
        nonce=nonce,
        signer_digest_alg_oid=signer_digest_alg_oid,
        signer_sig_alg_oid=signer_sig_alg_oid,
        signed_attrs_raw=signed_attrs_raw,
        signature=signature,
        certificates_der=certificates_der,
    )


def _signed_attr_value(signed_attrs_raw: bytes, attr_oid: str) -> Optional[bytes]:
    """Return the full TLV of the (single) value of an attribute, or None."""
    _, _, content, _ = asn1.read_tlv(signed_attrs_raw, 0)
    for tag, attr_content, _ in asn1.read_children(content):
        if tag != asn1.TAG_SEQUENCE:
            raise Rfc3161Error("malformed signed attribute")
        attr_children = asn1.read_children(attr_content)
        if len(attr_children) != 2 or attr_children[0][0] != asn1.TAG_OID:
            raise Rfc3161Error("malformed signed attribute")
        if asn1.decode_oid(attr_children[0][1]) != attr_oid:
            continue
        values = asn1.read_children(attr_children[1][1])
        if len(values) != 1:
            raise Rfc3161Error("signed attribute must have exactly one value")
        return values[0][2]
    return None


def signed_attrs_digest_input(signed_attrs_raw: bytes) -> bytes:
    """CMS rule: the signature is computed over the signedAttrs re-tagged as
    an EXPLICIT SET OF (0x31) instead of the [0] IMPLICIT (0xA0) wire tag."""
    if not signed_attrs_raw or signed_attrs_raw[0] != 0xA0:
        raise Rfc3161Error("signedAttrs must be [0] IMPLICIT tagged")
    return b"\x31" + signed_attrs_raw[1:]


# ---------------------------------------------------------------------------
# Pinned-key verification
# ---------------------------------------------------------------------------

def verify_timestamp_token(
    token_der: bytes,
    expected_imprint: bytes,
    pinned_spki_ders: Sequence[bytes],
    require_signature: bool = True,
) -> Dict[str, object]:
    """Verify a timestamp token against an expected imprint and pinned SPKIs.

    Returns {"valid": bool, "reason": str|None, "gen_time": str|None,
             "signature_checked": bool,
             "failure_kind": "token_imprint"|"token_signature"|None}.

    With require_signature=False and no pinned keys, only the structural and
    imprint/digest-binding checks run and "signature_checked" is False — the
    caller must report such witnesses as UNVERIFIED, never as verified.
    Fail closed: any parse error is a token_signature-class failure.
    """
    def _fail(kind: str, reason: str, gen_time: Optional[str] = None):
        return {"valid": False, "reason": reason, "gen_time": gen_time,
                "signature_checked": False, "failure_kind": kind}

    try:
        token = parse_timestamp_token(token_der)
    except (asn1.DerError, Rfc3161Error) as exc:
        return _fail("token_signature", f"token parse error: {exc}")
    except Exception as exc:  # noqa: BLE001 - see below; this is the fail-closed backstop
        # The contract this module advertises is "any parse error is a verification
        # failure", and the Python/Go/Node verifiers promise the same report shape
        # for the same input. A parser that lets an unexpected exception class
        # escape breaks both: the caller gets a crash rather than a denial, and a
        # surrounding pipeline may treat "verifier crashed" differently from
        # "verifier said INVALID". Attacker-supplied DER is exactly where a
        # catch-all belongs. The type is kept in the reason so a genuine bug is
        # still diagnosable rather than silently smoothed over.
        return _fail("token_signature",
                     f"token parse error ({type(exc).__name__}): {exc}")

    try:
        if token.imprint_alg_oid != OID_SHA256:
            return _fail("token_imprint",
                         f"imprint algorithm {token.imprint_alg_oid} is not SHA-256",
                         token.gen_time)
        if token.imprint_digest != expected_imprint:
            return _fail("token_imprint",
                         "messageImprint does not match the anchored chain head",
                         token.gen_time)

        # Signed-attribute bindings.
        ct = _signed_attr_value(token.signed_attrs_raw, OID_ATTR_CONTENT_TYPE)
        if ct is None:
            return _fail("token_signature", "missing content-type signed attribute",
                         token.gen_time)
        ct_tag, _, ct_content, _ = asn1.read_tlv(ct, 0)
        if ct_tag != asn1.TAG_OID or asn1.decode_oid(ct_content) != OID_CT_TSTINFO:
            return _fail("token_signature",
                         "content-type attribute is not id-ct-TSTInfo",
                         token.gen_time)

        md = _signed_attr_value(token.signed_attrs_raw, OID_ATTR_MESSAGE_DIGEST)
        if md is None:
            return _fail("token_signature", "missing message-digest signed attribute",
                         token.gen_time)
        md_tag, _, md_content, _ = asn1.read_tlv(md, 0)
        if md_tag != asn1.TAG_OCTET_STRING:
            return _fail("token_signature", "malformed message-digest attribute",
                         token.gen_time)
        if md_content != hashlib.sha256(token.tstinfo_der).digest():
            return _fail("token_signature",
                         "message-digest attribute does not match TSTInfo",
                         token.gen_time)

        if token.signer_digest_alg_oid != OID_SHA256:
            return _fail("token_signature",
                         f"signer digest algorithm {token.signer_digest_alg_oid} "
                         "is not SHA-256", token.gen_time)

        data = signed_attrs_digest_input(token.signed_attrs_raw)
    except (asn1.DerError, Rfc3161Error) as exc:
        return _fail("token_signature", f"token attribute error: {exc}")

    if not pinned_spki_ders:
        if require_signature:
            return _fail("token_signature", "no pinned TSA key supplied")
        return {"valid": True, "reason": None, "gen_time": token.gen_time,
                "signature_checked": False, "failure_kind": None}

    for spki_der in pinned_spki_ders:
        if _verify_sig_under_spki(spki_der, token.signer_sig_alg_oid, data,
                                  token.signature):
            return {"valid": True, "reason": None, "gen_time": token.gen_time,
                    "signature_checked": True, "failure_kind": None}
    return _fail("token_signature",
                 "signature does not verify under any pinned TSA key",
                 token.gen_time)


def _verify_sig_under_spki(spki_der: bytes, sig_alg_oid: str, data: bytes,
                           signature: bytes) -> bool:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
    from cryptography.hazmat.primitives.serialization import load_der_public_key

    try:
        pub = load_der_public_key(spki_der)
    except (ValueError, TypeError):
        return False

    try:
        if sig_alg_oid == OID_ECDSA_SHA256:
            if not isinstance(pub, ec.EllipticCurvePublicKey):
                return False
            pub.verify(signature, data, ec.ECDSA(hashes.SHA256()))
            return True
        if sig_alg_oid in (OID_RSA_SHA256, OID_RSA_ENCRYPTION):
            if not isinstance(pub, rsa.RSAPublicKey):
                return False
            pub.verify(signature, data, padding.PKCS1v15(), hashes.SHA256())
            return True
    except InvalidSignature:
        return False
    return False  # unsupported algorithm: fail closed


def extract_signer_spki_ders(token_der: bytes) -> List[bytes]:
    """Extract SPKI DER blobs from certificates embedded in a token.

    Convenience for key-pinning bootstrap ONLY. Extracting a key from a
    token and then pinning it proves nothing by itself — the operator must
    confirm the key out of band before trusting it.
    """
    from cryptography import x509
    from cryptography.hazmat.primitives.serialization import (
        Encoding, PublicFormat,
    )

    token = parse_timestamp_token(token_der)
    out: List[bytes] = []
    for cert_der in token.certificates_der:
        try:
            cert = x509.load_der_x509_certificate(cert_der)
            out.append(cert.public_key().public_bytes(
                Encoding.DER, PublicFormat.SubjectPublicKeyInfo))
        except ValueError:
            continue
    return out
