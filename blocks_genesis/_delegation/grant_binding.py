"""Check a received message's identity against its delegation grant.

The `SecurityContext` header is restored as sent. When the message also carries a grant, the grant
record names who the sender was, so a worker only runs the handler when the two agree.
"""

import logging
from typing import Optional

from blocks_genesis._auth.blocks_context import BlocksContext
from blocks_genesis._delegation.grant_store import DelegationGrantRecord, DelegationGrantStore
from blocks_genesis._delegation.signature import is_well_formed

logger = logging.getLogger(__name__)

# The Azure Service Bus dead-letter reason; the reason code below goes in its description.
GRANT_MISMATCH_DEAD_LETTER_REASON = "DelegationGrantMismatch"

# No usable record: missing, expired or evicted, or one the store could not decode (get_async
# returns None for an undecodable record too).
GRANT_NOT_FOUND = "grant_not_found"
CONTEXT_MISSING = "context_missing"
TENANT_MISMATCH = "tenant_mismatch"
USER_MISMATCH = "user_mismatch"
ORGANIZATION_MISMATCH = "organization_mismatch"
CLIENT_MISMATCH = "client_mismatch"


async def check_grant_binding(
    context: Optional[BlocksContext],
    delegation_grant: Optional[str],
    store: DelegationGrantStore,
    message_id: Optional[str] = None,
) -> Optional[str]:
    """None when the handler may run; otherwise the reason it must not.

    Only a message that carries a well-formed grant is checked. A message with no grant is
    left exactly as it was (see card #39 follow-ups).

    A store failure is not caught: it follows the worker's own failure path.
    """
    if not is_well_formed(delegation_grant):
        return None

    record = await store.get_async(delegation_grant)
    reason = _mismatch(context, record)
    if reason:
        # The grant id is a credential and the header may carry personal data, so neither is logged.
        logger.error(
            "Message %s not handled: delegation grant check failed (%s); "
            "context tenant %r, grant tenant %r",
            message_id,
            reason,
            ((context.tenant_id if context else "") or "-")[:64],
            (record.tenant_id if record else "") or "-",
        )
    return reason


def _mismatch(context: Optional[BlocksContext], record: Optional[DelegationGrantRecord]) -> Optional[str]:
    if record is None:
        return GRANT_NOT_FOUND
    if context is None:
        return CONTEXT_MISSING
    if context.tenant_id != record.tenant_id:
        return TENANT_MISMATCH
    if context.user_id != record.user_id:
        return USER_MISMATCH
    if (context.organization_id or "") != (record.organization_id or ""):
        return ORGANIZATION_MISMATCH
    # A user token can carry the client it was issued through; only a client grant names one.
    if record.is_client_grant and context.client_id != record.client_id:
        return CLIENT_MISMATCH
    return None
