"""Builds the grant a send attaches to a message.

Shared by the Azure and RabbitMQ clients so both produce identical headers.

`token_version` and `security_stamp` come from the validated token's claims, captured into
`AuthClaimsContext` during authentication, so a send costs no extra I/O. A worker-originated send
(chained delegation) has no request claims; there the two values are carried forward from the grant
the worker is already holding.

With no authenticated user in context there is no grant and the header is omitted: the flow fails
closed rather than minting a token nobody asked for.
"""

import logging
from typing import Optional, Tuple

from blocks_genesis._auth.blocks_context import BlocksContextManager
from blocks_genesis._delegation.context import AuthClaimsContext, DelegatedTokenContext
from blocks_genesis._delegation.grant_store import DelegationGrantStore, get_delegation_grant_store

logger = logging.getLogger(__name__)


class DelegationGrantFactory:
    def __init__(self, grant_store: Optional[DelegationGrantStore] = None) -> None:
        self._grant_store = grant_store

    @property
    def _store(self) -> DelegationGrantStore:
        return self._grant_store or get_delegation_grant_store()

    async def create_for_send_async(self, ttl_seconds: Optional[int] = None) -> Optional[str]:
        """One grant per logical message. Never reused across messages."""
        context = BlocksContextManager.get_context()

        if context is None or not context.is_authenticated or not context.tenant_id or not context.user_id:
            return None

        token_version, security_stamp = AuthClaimsContext.version_material()

        if not token_version and not security_stamp:
            token_version, security_stamp = await self._read_from_held_grant(context.tenant_id)

        if not token_version or not security_stamp:
            # A grant without these cannot be redeemed: IAM compares both against the tenant DB.
            logger.debug(
                "No token_version/security_stamp available for the current flow; "
                "sending without a delegation grant."
            )
            return None

        try:
            return await self._store.create_async(context, token_version, security_stamp, ttl_seconds)
        except Exception as ex:  # noqa: BLE001
            # A send must not fail because delegation could not be set up. The message still goes
            # out, just without user context downstream.
            logger.error("Could not create a delegation grant; the message is sent without one: %s", ex)
            return None

    async def _read_from_held_grant(self, tenant_id: str) -> Tuple[Optional[str], Optional[str]]:
        held_grant_id = DelegatedTokenContext.current()
        if not held_grant_id:
            return None, None

        record = await self._store.get_async(held_grant_id)
        if record is None:
            return None, None

        if record.tenant_id != tenant_id:
            logger.warning(
                "The held delegation grant belongs to a different tenant than the current context; "
                "not chaining it."
            )
            return None, None

        return record.token_version, record.security_stamp


_factory: Optional[DelegationGrantFactory] = None


def get_delegation_grant_factory() -> DelegationGrantFactory:
    global _factory
    if _factory is None:
        _factory = DelegationGrantFactory()
    return _factory


def set_delegation_grant_factory(factory: Optional[DelegationGrantFactory]) -> None:
    global _factory
    _factory = factory
