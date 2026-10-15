"""Refuse a received message whose tenant is missing or disabled, shared by both workers."""

import logging
from typing import Optional

from blocks_genesis._auth.blocks_context import BlocksContext
from blocks_genesis._tenant.tenant_service import get_tenant_service

logger = logging.getLogger(__name__)

# The Azure Service Bus dead-letter reason; the reason code below goes in its description.
TENANT_UNAVAILABLE_DEAD_LETTER_REASON = "TenantUnavailable"
TENANT_UNAVAILABLE = "tenant_unavailable"


async def check_message_tenant(context: Optional[BlocksContext], message_id: Optional[str] = None) -> Optional[str]:
    """None when the handler may run; TENANT_UNAVAILABLE when the context's tenant is missing or disabled.

    A message whose context names no tenant is not checked. When the root database cannot be read
    and no copy is cached, TenantReadError is raised and follows the worker's own failure path.
    """
    tenant_id = context.tenant_id if context else ""
    if not tenant_id:
        return None
    if await get_tenant_service().resolve_tenant(tenant_id) is not None:
        return None
    logger.error("Message %s not handled: %s; tenant %r", message_id, TENANT_UNAVAILABLE, tenant_id[:64])
    return TENANT_UNAVAILABLE
