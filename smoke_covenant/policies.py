"""Policies beyond the toy. Still NOT a rights engine -- read this first.

`toy_territory_window_policy` in grants.py understands territories, dates and
channels. Registering three REAL model licences broke it immediately, because the
fact that actually separates them is one the toy vocabulary cannot express:

    sd_xl_base_1.0        CreativeML OpenRAIL++-M   commercial PERMITTED, w/ use restrictions
    pixel-art-xl          CreativeML OpenRAIL-M     commercial PERMITTED, w/ use restrictions
    dmd2_sdxl_4step_lora  CC-BY-NC-4.0              commercial PROHIBITED

That is the whole finding, and it is the one worth having: contact with real
licences, not more design, is what exposes a schema's missing column.

So this module adds exactly the two columns those licences actually use --
`commercial_use` and `use_restrictions` -- and stops. It is still not rights
engineering. It does not model sublicensing, derivative works, attribution
obligations, moral rights, likeness, territory carve-outs, per-channel windows,
or any of the machinery an agency's business affairs team applies. Those are
where acceptance lives, and acceptance cannot be built, only won.

WHAT THIS DOES NOT DO, restated because it is the thing that gets misread:
it does not INTERPRET a licence. A human read those licences and wrote the terms
into a Grant. This only re-applies that human's structured reading, consistently,
at the moment of use, and commits to the result. Ask a lawyer what a licence
means; ask this whether the answer was applied to the bytes that shipped.
"""

from __future__ import annotations

from typing import Mapping

from ._vendor.primitives import canonical_json_bytes

from .grants import Grant, PredicatePolicy

# OpenRAIL Attachment A, condensed. Real deployments should carry the full list
# and its exact wording; these identifiers exist so the demo can express that a
# permissive-for-commerce licence still carries conditions.
OPENRAIL_RESTRICTED_USES = (
    "discrimination",
    "disinformation",
    "medical_advice_unqualified",
    "legal_advice_unqualified",
    "law_enforcement_profiling",
    "harassment",
)


def media_licence_policy() -> PredicatePolicy:
    """Applies commercial-use and use-restriction terms, plus the toy's fields.

    Grant.terms keys understood:
        commercial_use    "permitted" | "prohibited"     (absent => unconstrained)
        use_restrictions  [str]  uses the licence forbids
        territories       [str]  (as the toy policy)
        expires_on        ISO date string
        channels          [str]

    Render context keys read:
        commercial        bool    is this render for a commercial purpose
        intended_uses     [str]   what the render is for
        territory, release_end, channels
    """

    def predicate(grant: Grant, ctx: Mapping[str, object]) -> bool:
        terms = grant.terms

        # The column the toy policy lacked, and the one that decides real assets.
        if terms.get("commercial_use") == "prohibited" and ctx.get("commercial"):
            return False

        # A licence that permits commerce can still forbid specific uses.
        forbidden = set(terms.get("use_restrictions") or ())
        if forbidden & set(ctx.get("intended_uses") or ()):
            return False

        territories = terms.get("territories")
        if territories is not None and ctx.get("territory") not in territories:
            return False

        expires = terms.get("expires_on")
        release = ctx.get("release_end")
        if expires is not None and release is not None and str(release) > str(expires):
            return False

        channels = terms.get("channels")
        if channels is not None:
            if not set(ctx.get("channels") or ()).issubset(set(channels)):
                return False

        return True

    return PredicatePolicy(
        policy_id="media-licence-v0",
        predicate=predicate,
        source=(
            "commercial_use + use_restrictions + territories/expires_on/channels. "
            "NOT a rights engine: no sublicensing, derivative-works, attribution, "
            "moral-rights, likeness or per-channel-window semantics."
        ),
    )


class ExternalRightsPolicy:
    """Delegate the decision to a real rights system. THE INTENDED PRODUCTION SHAPE.

    The temptation with this file is to keep growing it -- add territories, then
    sublicensing, then derivative works, then likeness, then per-channel windows --
    until it is a half-built rights engine that a studio's business-affairs team
    correctly refuses to trust. That path has no end and no buyer: encoding law is
    where acceptance lives, and acceptance cannot be built, only won.

    So the extension point is a SEAM, not a schema. Point this at Rightsline, a
    DAM, an internal clearance service, or a human-in-the-loop queue. This package
    keeps doing the part it can actually be right about -- hashing the bytes,
    enforcing at the moment of use, and committing to the result -- and lets the
    system that already owns rights semantics decide.

    `decide(grant, context) -> (allowed: bool, reason: str)`. Any exception is a
    DENY, not a pass: a rights service that is down is not a rights service that
    said yes. `policy_hash` covers the resolver's identity and version so the
    covenant records WHICH rights system ruled, and a verifier can tell that a
    decision made under v3 was not made under v4.
    """

    def __init__(self, policy_id: str, resolver, version: str = "unversioned"):
        self.policy_id = policy_id
        self._resolver = resolver
        self._version = version

    def policy_hash(self) -> str:
        from .grants import digest_bytes
        return digest_bytes(canonical_json_bytes({
            "id": self.policy_id,
            "resolver": type(self._resolver).__name__,
            "version": self._version,
        }))

    def evaluate(self, grant: Grant, context: Mapping[str, object]) -> None:
        from .grants import PolicyDenied
        try:
            allowed, reason = self._resolver.decide(grant, context)
        except Exception as exc:  # noqa: BLE001 - unreachable rights service => deny
            raise PolicyDenied(
                f"{self.policy_id}: rights resolver unavailable, denying "
                f"({type(exc).__name__}: {exc})"
            ) from exc
        if not allowed:
            raise PolicyDenied(f"{self.policy_id}: {reason}")


# --- The three real licences, as a human read them ---------------------------
# Transcribed from the published licence texts, NOT machine-derived. Each is one
# person's structured reading and should be reviewed before any real use.

LICENCE_TERMS = {
    "cc-by-nc-4.0": {
        "licence": "CC-BY-NC-4.0",
        "commercial_use": "prohibited",
        "attribution_required": True,
        "source": "https://creativecommons.org/licenses/by-nc/4.0/",
    },
    "creativeml-openrail-m": {
        "licence": "CreativeML OpenRAIL-M",
        "commercial_use": "permitted",
        "use_restrictions": list(OPENRAIL_RESTRICTED_USES),
        "source": "https://huggingface.co/spaces/CompVis/stable-diffusion-license",
    },
    "creativeml-openrail++-m": {
        "licence": "CreativeML OpenRAIL++-M",
        "commercial_use": "permitted",
        "use_restrictions": list(OPENRAIL_RESTRICTED_USES),
        "source": "https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/blob/main/LICENSE.md",
    },
}
