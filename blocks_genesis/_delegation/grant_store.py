"""Redis-backed delegation grant storage.

The record — not the message `SecurityContext` — is what IAM trusts when minting a delegated
access token. JSON field names are PascalCase so blocks-genesis-net and blocks-iam read the
same document.
"""

import json
import logging
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from blocks_genesis._auth.blocks_context import BlocksContext
from blocks_genesis._cache.cache_provider import CacheProvider
from blocks_genesis._delegation.constants import DEFAULT_GRANT_TTL_SECONDS, grant_key
from blocks_genesis._delegation.signature import is_well_formed, new_grant_id

logger = logging.getLogger(__name__)


class DelegationGrantRecord(BaseModel):
    """The authoritative identity behind a delegation grant."""

    tenant_id: str = Field(alias="TenantId", default="")
    user_id: str = Field(alias="UserId", default="")
    organization_id: str = Field(alias="OrganizationId", default="")
    token_version: str = Field(alias="TokenVersion", default="")
    security_stamp: str = Field(alias="SecurityStamp", default="")

    class Config:
        extra = "ignore"
        populate_by_name = True

    def to_wire_json(self) -> str:
        """Serialize with the PascalCase names the other SDKs expect."""
        return json.dumps(self.model_dump(by_alias=True))


class DelegationGrantStore:
    """Writes and removes delegation grants.

    A grant is created at send time, while a validated user token is still in scope, and removed
    after the worker settles the message. There is no sliding TTL, no heartbeat and no cleanup
    scheduler: the absolute TTL is the backstop.
    """

    def __init__(self, cache_client: Any = None) -> None:
        self._cache_client = cache_client

    @property
    def _cache(self) -> Any:
        return self._cache_client or CacheProvider.get_client()

    async def create_async(
        self,
        context: BlocksContext,
        token_version: str,
        security_stamp: str,
        ttl_seconds: Optional[int] = None,
    ) -> str:
        """Persist a grant and return its opaque id."""
        if context is None or not context.tenant_id or not context.user_id:
            raise ValueError("A delegation grant requires both a tenant and an authenticated user.")

        record = DelegationGrantRecord(
            TenantId=context.tenant_id,
            UserId=context.user_id,
            OrganizationId=context.organization_id or "",
            TokenVersion=token_version or "",
            SecurityStamp=security_stamp or "",
        )

        delegation_id = new_grant_id()
        lifetime = ttl_seconds if ttl_seconds and ttl_seconds > 0 else DEFAULT_GRANT_TTL_SECONDS

        # setex writes value and TTL in one command, so a grant can never exist without an expiry.
        await self._cache.add_string_value_async(grant_key(delegation_id), record.to_wire_json(), lifetime)

        # The id is a bearer credential: it is never logged, traced, or put in baggage.
        logger.debug("Delegation grant created for tenant %s with a %ss lifetime", record.tenant_id, lifetime)

        return delegation_id

    async def delete_async(self, delegation_id: Optional[str]) -> None:
        """Best-effort removal after a successful settle. Never called before the ACK."""
        if not is_well_formed(delegation_id):
            return

        try:
            await self._cache.remove_key_async(grant_key(delegation_id))
        except Exception as ex:  # noqa: BLE001 - the absolute TTL still bounds the grant
            logger.warning("Failed to delete a delegation grant; the TTL will remove it: %s", ex)

    async def get_async(self, delegation_id: Optional[str]) -> Optional[DelegationGrantRecord]:
        """Read a grant record.

        Used only for chained delegation: a worker-originated send carries TokenVersion and
        SecurityStamp forward from the grant it is already holding.
        """
        if not is_well_formed(delegation_id):
            return None

        raw = await self._cache.get_string_value_async(grant_key(delegation_id))
        if not raw:
            return None

        try:
            payload: Dict[str, Any] = json.loads(raw)
            return DelegationGrantRecord(**payload)
        except Exception as ex:  # noqa: BLE001
            logger.warning("Stored delegation grant could not be deserialized: %s", ex)
            return None


_grant_store: Optional[DelegationGrantStore] = None


def get_delegation_grant_store() -> DelegationGrantStore:
    """The process-wide store. Resolves its cache client lazily, on first use."""
    global _grant_store
    if _grant_store is None:
        _grant_store = DelegationGrantStore()
    return _grant_store


def set_delegation_grant_store(store: Optional[DelegationGrantStore]) -> None:
    """Replace the process-wide store. Intended for tests and custom hosting."""
    global _grant_store
    _grant_store = store
