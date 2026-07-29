"""RFC 3161 CLIENT -- request a trusted timestamp over HTTP, for standalone use.

`rfc3161.py` in this same package already carries the VERIFY side (parse a
token, check it against a pinned key) plus the low-level pieces a client needs
to build a request and parse a response: `build_timestamp_request`,
`parse_timestamp_response`, `parse_timestamp_token`, `message_imprint_digest`.
This module is only the HTTP plumbing around those -- it does not reimplement
any DER encoding/decoding. If a change is needed to how a request or response
is shaped, it belongs in `rfc3161.py`, not here, or the two copies (this one
and smoke_trust's) will silently drift apart.

Mirrors smoke_trust.audit.anchor.TSAClient's behaviour and default endpoints
exactly, so a covenant anchored with this client verifies identically to one
anchored with smoke_trust's: request_witness() NEVER raises -- any failure
(network, malformed response, refused status, mismatched imprint or nonce)
comes back as a structured error witness dict instead, because an anchoring
failure must be recordable in the covenant, not fatal to issuing it.
"""

from __future__ import annotations

import base64
import secrets
import urllib.error
import urllib.request
from typing import Any, Dict, List

from .rfc3161 import (
    build_timestamp_request,
    message_imprint_digest,
    parse_timestamp_response,
    parse_timestamp_token,
)

# Same defaults as smoke_trust.audit.anchor -- any RFC 3161 endpoint works for
# the commercial slot (DigiCert shown; Sectigo: http://timestamp.sectigo.com).
DEFAULT_COMMERCIAL_TSA_URL = "http://timestamp.digicert.com"
DEFAULT_SIGSTORE_TSA_URL = "https://timestamp.sigstore.dev/api/v1/timestamp"


class TSAClient:
    """Minimal RFC 3161 client over HTTP POST (application/timestamp-query).

    request_witness() NEVER raises: any failure is returned as an error
    witness dict so the caller can record it loudly alongside the covenant
    rather than losing the ability to issue one at all.
    """

    def __init__(self, url: str, timeout_s: float = 10.0):
        self.url = url
        self.timeout_s = timeout_s

    def request_witness(self, anchored_hash_hex: str) -> Dict[str, Any]:
        try:
            imprint = message_imprint_digest(anchored_hash_hex)
            nonce = secrets.randbits(64)
            req_der = build_timestamp_request(imprint, nonce=nonce, cert_req=True)
            request = urllib.request.Request(
                self.url,
                data=req_der,
                headers={"Content-Type": "application/timestamp-query"},
                method="POST",
            )
            with urllib.request.urlopen(request, timeout=self.timeout_s) as resp:
                body = resp.read()
            ts_resp = parse_timestamp_response(body)
            if not ts_resp.granted:
                detail = "; ".join(ts_resp.status_strings) or "no status text"
                return self._error(f"TSA refused: status={ts_resp.status} ({detail})")
            if ts_resp.token_der is None:
                return self._error("TSA granted but returned no token")

            token = parse_timestamp_token(ts_resp.token_der)
            if token.imprint_digest != imprint:
                return self._error("TSA token imprint does not match request")
            if token.nonce is not None and token.nonce != nonce:
                return self._error("TSA token nonce does not match request")

            return {
                "type": "rfc3161",
                "url": self.url,
                "status": "ok",
                "gen_time": token.gen_time,
                "token_b64": base64.b64encode(ts_resp.token_der).decode("ascii"),
            }
        except Exception as exc:  # noqa: BLE001 -- anchoring must never raise
            return self._error(f"{type(exc).__name__}: {exc}")

    def _error(self, message: str) -> Dict[str, Any]:
        return {"type": "rfc3161", "url": self.url, "status": "error",
                "error": message}


def default_tsa_clients(
    commercial_url: str = DEFAULT_COMMERCIAL_TSA_URL,
    sigstore_url: str = DEFAULT_SIGSTORE_TSA_URL,
    timeout_s: float = 10.0,
) -> List[TSAClient]:
    """The two-TSA default: one commercial CA endpoint + the Sigstore TSA."""
    return [TSAClient(commercial_url, timeout_s=timeout_s),
            TSAClient(sigstore_url, timeout_s=timeout_s)]
