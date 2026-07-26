"""Render Covenants — hermetic build provenance for media.

NOT a clearance system. It does not decide rights; it records that a policy was
applied to the ingredients a render actually read, and binds that result to the
exact delivered bytes. See `grants.py` for the full non-goals list, and carry
them with any external description of this format.
"""

from .covenant import (
    COVENANT_VERSION,
    Covenant,
    CovenantInvalid,
    issue,
    prove_ingredient,
    verify,
    verify_ingredient,
)
from .gate import HermeticGate, Ingredient
from .grants import (
    AssetNotRegistered,
    AssetStore,
    CovenantError,
    Grant,
    PolicyDenied,
    PredicatePolicy,
    digest_bytes,
    digest_file,
    toy_territory_window_policy,
)

__all__ = [
    "COVENANT_VERSION",
    "AssetNotRegistered",
    "AssetStore",
    "Covenant",
    "CovenantError",
    "CovenantInvalid",
    "Grant",
    "HermeticGate",
    "Ingredient",
    "PolicyDenied",
    "PredicatePolicy",
    "digest_bytes",
    "digest_file",
    "issue",
    "prove_ingredient",
    "toy_territory_window_policy",
    "verify",
    "verify_ingredient",
]
