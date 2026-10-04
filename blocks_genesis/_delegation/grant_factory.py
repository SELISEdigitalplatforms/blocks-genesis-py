"""Builds the grant a send attaches to a message.

Shared by the Azure and RabbitMQ clients so both produce identical headers.

`token_version` and `security_stamp` come from the validated token's claims, captured into
`AuthClaimsContext` during authentication, so a send costs no extra I/O. A worker-originated send
(chained delegation) has no request claims; there the two values are carried forward from the grant
the worker is already holding.

A `client_credentials` caller (a token with `client_id` and no `user_id`) gets a client grant
instead, which needs no version material.

With neither an authenticated user nor a client in context there is no grant and the header is
omitted: the flow fails closed rather than minting a token nobody asked for.
"""

import logging
from typing import Optional, Tuple

from blocks_genesis._auth.blocks_context import BlocksContextManager
from blocks_genesis._delegation.context import AuthClaimsContext, DelegatedTokenContext
from blocks_genesis._delegation.grant_store import (
    DelegationGrantRecord,
    DelegationGrantStore,
    get_delegation_grant_store,
)

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

        if context is None or not context.is_authenticated or not context.tenant_id:
            # Said out loud, because the cost of this being silent is paid downstream and far
            # away: the message is sent, the consumer has no caller, and every Blocks call it
            # makes is skipped with nothing anywhere saying why.
            logger.debug("No authenticated caller in context; sending without a delegation grant.")
            return None

        # A user token may also carry client_id (the OIDC client it was issued to); the user wins.
        if context.user_id:
            return await self._create_for_user(context, ttl_seconds)

        if getattr(context, "client_id", ""):
            return await self._create_for_client(context, ttl_seconds)

        logger.debug(
            "The caller in context names neither a user nor a client; sending without a "
            "delegation grant."
        )
        return None

    async def _create_for_client(self, context, ttl_seconds: Optional[int]) -> Optional[str]:
        """A client_credentials caller.

        In an API request the client id comes from the validated token's claims. In a worker it
        comes from the held grant, never from the message SecurityContext alone: a client grant is
        only chained from another client grant.
        """
        claims = AuthClaimsContext.current() or {}
        claims_client_id = None if claims.get("user_id") else claims.get("client_id")

        if claims_client_id:
            client_id = str(claims_client_id)
            organization_id = context.organization_id
        else:
            held = await self._read_held_grant(context.tenant_id)
            if held is None or not held.is_client_grant:
                logger.debug(
                    "Client context with no validated client token and no held client grant; "
                    "sending without a delegation grant."
                )
                return None

            if held.client_id != context.client_id:
                logger.warning(
                    "The held client delegation grant names a different client than the current "
                    "context; not chaining it."
                )
                return None

            client_id = held.client_id
            organization_id = held.organization_id

        try:
            return await self._store.create_for_client_async(
                context.tenant_id, client_id, organization_id, ttl_seconds
            )
        except Exception as ex:  # noqa: BLE001
            logger.error("Could not create a delegation grant; the message is sent without one: %s", ex)
            return None

    async def _create_for_user(self, context, ttl_seconds: Optional[int]) -> Optional[str]:
        token_version, security_stamp = AuthClaimsContext.version_material()

        if not token_version and not security_stamp:
            token_version, security_stamp = await self._read_from_held_grant(context)

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

    async def _read_from_held_grant(self, context) -> Tuple[Optional[str], Optional[str]]:
        """In a worker the user and organization on the context came from the message
        SecurityContext. The new grant is written from that context, so it must name the same user
        and organization as the held grant, or a tampered message could redirect the chain.
        """
        record = await self._read_held_grant(context.tenant_id)
        if record is None or record.is_client_grant:
            return None, None

        if record.user_id != context.user_id or (record.organization_id or "") != (context.organization_id or ""):
            logger.warning(
                "The held delegation grant names a different user or organization than the current "
                "context; not chaining it."
            )
            return None, None

        return record.token_version, record.security_stamp

    async def _read_held_grant(self, tenant_id: str) -> Optional[DelegationGrantRecord]:
        held_grant_id = DelegatedTokenContext.current()
        if not held_grant_id:
            return None

        record = await self._store.get_async(held_grant_id)
        if record is None:
            return None

        if record.tenant_id != tenant_id:
            logger.warning(
                "The held delegation grant belongs to a different tenant than the current context; "
                "not chaining it."
            )
            return None

        return record


_factory: Optional[DelegationGrantFactory] = None


def get_delegation_grant_factory() -> DelegationGrantFactory:
    global _factory
    if _factory is None:
        _factory = DelegationGrantFactory()
    return _factory


def set_delegation_grant_factory(factory: Optional[DelegationGrantFactory]) -> None:
    global _factory
    _factory = factory
