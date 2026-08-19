"""Ambient holders for delegated access.

`DelegatedTokenContext` carries the grant id the current unit of work may redeem. The worker sets
it from the `DelegationGrant` message header before dispatching to the handler and clears it after
the message settles. The value is a bearer credential: it must never be logged, set as a span
attribute, or placed in baggage.

`AuthClaimsContext` carries `token_version` and `security_stamp` from the validated token. They are
not on `BlocksContext`, and a send needs them to build a grant without extra I/O.
"""

from contextvars import ContextVar
from typing import Any, Dict, Optional, Tuple

from blocks_genesis._delegation.constants import SECURITY_STAMP_CLAIM, TOKEN_VERSION_CLAIM
from blocks_genesis._delegation.signature import is_well_formed

_delegation_grant_var: ContextVar[Optional[str]] = ContextVar("blocks_delegation_grant", default=None)
_auth_claims_var: ContextVar[Optional[Dict[str, Any]]] = ContextVar("blocks_auth_claims", default=None)


class DelegatedTokenContext:
    """The delegation grant id for the current logical flow."""

    @staticmethod
    def set(delegation_grant_id: Optional[str]) -> None:
        """Malformed values are treated as absent, so the flow fails closed."""
        _delegation_grant_var.set(delegation_grant_id if is_well_formed(delegation_grant_id) else None)

    @staticmethod
    def current() -> Optional[str]:
        return _delegation_grant_var.get()

    @staticmethod
    def has_grant() -> bool:
        return bool(_delegation_grant_var.get())

    @staticmethod
    def clear() -> None:
        _delegation_grant_var.set(None)


class AuthClaimsContext:
    """Version material from the validated token, used only at send time."""

    @staticmethod
    def set(claims: Optional[Dict[str, Any]]) -> None:
        _auth_claims_var.set(claims)

    @staticmethod
    def current() -> Optional[Dict[str, Any]]:
        return _auth_claims_var.get()

    @staticmethod
    def clear() -> None:
        _auth_claims_var.set(None)

    @staticmethod
    def version_material() -> Tuple[Optional[str], Optional[str]]:
        """`(token_version, security_stamp)` as strings, or `(None, None)` when unavailable."""
        claims = _auth_claims_var.get()
        if not claims:
            return None, None

        token_version = claims.get(TOKEN_VERSION_CLAIM)
        security_stamp = claims.get(SECURITY_STAMP_CLAIM)

        return (
            None if token_version is None else str(token_version),
            None if security_stamp is None else str(security_stamp),
        )
