"""Minimal DER (ASN.1) encode/decode primitives for RFC 3161 structures.

Deliberately tiny: only the encoding forms needed to build a TimeStampReq
and walk a TimeStampResp/TimeStampToken (CMS SignedData). Definite-length
DER only; indefinite lengths and multi-byte tags are rejected (fail closed).

This module performs NO cryptography — it is pure byte structure. Signature
verification lives in rfc3161.py against pinned keys.
"""

from __future__ import annotations

from typing import List, Tuple

# Universal tags
TAG_BOOLEAN = 0x01
TAG_INTEGER = 0x02
TAG_BIT_STRING = 0x03
TAG_OCTET_STRING = 0x04
TAG_NULL = 0x05
TAG_OID = 0x06
TAG_UTF8_STRING = 0x0C
TAG_SEQUENCE = 0x30
TAG_SET = 0x31
TAG_GENERALIZED_TIME = 0x18


class DerError(ValueError):
    """Malformed or unsupported DER. Callers must treat as verification failure."""


# ---------------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------------

def encode_length(n: int) -> bytes:
    if n < 0x80:
        return bytes([n])
    body = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(body)]) + body


def encode_tlv(tag: int, content: bytes) -> bytes:
    return bytes([tag]) + encode_length(len(content)) + content


def encode_sequence(*parts: bytes) -> bytes:
    return encode_tlv(TAG_SEQUENCE, b"".join(parts))


def encode_set(*parts: bytes) -> bytes:
    return encode_tlv(TAG_SET, b"".join(parts))


def encode_integer(value: int) -> bytes:
    if value == 0:
        return encode_tlv(TAG_INTEGER, b"\x00")
    if value < 0:
        raise DerError("negative INTEGER encoding not supported")
    body = value.to_bytes((value.bit_length() + 7) // 8, "big")
    if body[0] & 0x80:
        body = b"\x00" + body
    return encode_tlv(TAG_INTEGER, body)


def encode_boolean(value: bool) -> bytes:
    return encode_tlv(TAG_BOOLEAN, b"\xff" if value else b"\x00")


def encode_null() -> bytes:
    return encode_tlv(TAG_NULL, b"")


def encode_octet_string(data: bytes) -> bytes:
    return encode_tlv(TAG_OCTET_STRING, data)


def encode_oid(dotted: str) -> bytes:
    arcs = [int(a) for a in dotted.split(".")]
    if len(arcs) < 2:
        raise DerError(f"OID too short: {dotted}")
    body = bytearray([40 * arcs[0] + arcs[1]])
    for arc in arcs[2:]:
        chunk = bytearray()
        chunk.append(arc & 0x7F)
        arc >>= 7
        while arc:
            chunk.append(0x80 | (arc & 0x7F))
            arc >>= 7
        body.extend(reversed(chunk))
    return encode_tlv(TAG_OID, bytes(body))


def encode_generalized_time(ts: str) -> bytes:
    """ts is an ASCII GeneralizedTime string, e.g. '20260712120000Z'."""
    return encode_tlv(TAG_GENERALIZED_TIME, ts.encode("ascii"))


def encode_utf8_string(s: str) -> bytes:
    return encode_tlv(TAG_UTF8_STRING, s.encode("utf-8"))


def encode_explicit(tag_no: int, content: bytes) -> bytes:
    """Context-specific constructed [tag_no] EXPLICIT wrapper."""
    return encode_tlv(0xA0 | tag_no, content)


def encode_implicit(tag_no: int, inner_tlv: bytes, constructed: bool = True) -> bytes:
    """Retag an already-encoded TLV as context-specific [tag_no] IMPLICIT."""
    tag = (0xA0 if constructed else 0x80) | tag_no
    _, _, content, _ = read_tlv(inner_tlv, 0)
    return encode_tlv(tag, content)


# ---------------------------------------------------------------------------
# Decoding
# ---------------------------------------------------------------------------

def read_tlv(buf: bytes, offset: int) -> Tuple[int, int, bytes, int]:
    """Read one TLV at offset.

    Returns (tag, header_len, content_bytes, end_offset). Rejects multi-byte
    tags and indefinite lengths.
    """
    if offset >= len(buf):
        raise DerError("truncated DER: no tag byte")
    tag = buf[offset]
    if tag & 0x1F == 0x1F:
        raise DerError("multi-byte DER tags not supported")
    if offset + 1 >= len(buf):
        raise DerError("truncated DER: no length byte")
    first_len = buf[offset + 1]
    if first_len == 0x80:
        raise DerError("indefinite DER length not allowed")
    if first_len < 0x80:
        length = first_len
        header = 2
    else:
        n = first_len & 0x7F
        if n > 4:
            raise DerError("DER length too large")
        if offset + 2 + n > len(buf):
            raise DerError("truncated DER length")
        length = int.from_bytes(buf[offset + 2:offset + 2 + n], "big")
        header = 2 + n
    end = offset + header + length
    if end > len(buf):
        raise DerError("truncated DER content")
    return tag, header, buf[offset + header:end], end


def read_children(content: bytes) -> List[Tuple[int, bytes, bytes]]:
    """Split a constructed value's content into child (tag, content, full_tlv) tuples."""
    out: List[Tuple[int, bytes, bytes]] = []
    off = 0
    while off < len(content):
        tag, _, child, end = read_tlv(content, off)
        out.append((tag, child, content[off:end]))
        off = end
    return out


def expect_tag(tag: int, expected: int, what: str) -> None:
    if tag != expected:
        raise DerError(f"{what}: expected tag 0x{expected:02x}, got 0x{tag:02x}")


def decode_oid(content: bytes) -> str:
    if not content:
        raise DerError("empty OID")
    arcs = [content[0] // 40, content[0] % 40]
    val = 0
    for b in content[1:]:
        val = (val << 7) | (b & 0x7F)
        if not b & 0x80:
            arcs.append(val)
            val = 0
    if content[-1] & 0x80:
        raise DerError("truncated OID arc")
    return ".".join(str(a) for a in arcs)


def decode_integer(content: bytes) -> int:
    if not content:
        raise DerError("empty INTEGER")
    return int.from_bytes(content, "big", signed=True)
