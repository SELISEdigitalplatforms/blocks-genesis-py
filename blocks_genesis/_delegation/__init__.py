"""Delegated access: an opaque grant written at send time, redeemed at IAM by the worker.

Mirrors `Genesis/Delegation/` in blocks-genesis-net. The wire contract -- id format, signature
input, Redis key names, JSON field names, header names -- is identical in both SDKs and asserted
by a shared conformance vector.
"""

from blocks_genesis._delegation.constants import (
    BLOCKS_KEY_HEADER,
    CLOCK_WINDOW_SECONDS,
    DEFAULT_GRANT_TTL_SECONDS,
    DELEGATION_GRANT_HEADER,
    DELEGATION_GRANT_TOKEN_TYPE,
    GRANT_ID_PREFIX,
    GRANT_KEY_PREFIX,
    NONCE_KEY_PREFIX,
    NONCE_TTL_SECONDS,
    REDEMPTION_KEY_PREFIX,
    TOKEN_EXCHANGE_GRANT_TYPE,
    TOKEN_RENEWAL_MARGIN_SECONDS,
    build_signature_input,
    grant_key,
)
from blocks_genesis._delegation.context import AuthClaimsContext, DelegatedTokenContext
from blocks_genesis._delegation.endpoint_resolver import (
    DelegationTokenEndpointResolver,
    get_endpoint_resolver,
    set_endpoint_resolver,
)
from blocks_genesis._delegation.grant_factory import (
    DelegationGrantFactory,
    get_delegation_grant_factory,
    set_delegation_grant_factory,
)
from blocks_genesis._delegation.grant_store import (
    DelegationGrantRecord,
    DelegationGrantStore,
    get_delegation_grant_store,
    set_delegation_grant_store,
)
from blocks_genesis._delegation.signature import (
    compute,
    is_well_formed,
    new_grant_id,
    new_nonce,
    sign,
    verify,
)
from blocks_genesis._delegation.token_provider import (
    DelegatedTokenProvider,
    delegated_auth_headers,
    get_delegated_token_provider,
    set_delegated_token_provider,
)

__all__ = [
    "AuthClaimsContext",
    "BLOCKS_KEY_HEADER",
    "CLOCK_WINDOW_SECONDS",
    "DEFAULT_GRANT_TTL_SECONDS",
    "DELEGATION_GRANT_HEADER",
    "DELEGATION_GRANT_TOKEN_TYPE",
    "DelegatedTokenContext",
    "DelegatedTokenProvider",
    "DelegationGrantFactory",
    "DelegationGrantRecord",
    "DelegationGrantStore",
    "DelegationTokenEndpointResolver",
    "GRANT_ID_PREFIX",
    "GRANT_KEY_PREFIX",
    "NONCE_KEY_PREFIX",
    "NONCE_TTL_SECONDS",
    "REDEMPTION_KEY_PREFIX",
    "TOKEN_EXCHANGE_GRANT_TYPE",
    "TOKEN_RENEWAL_MARGIN_SECONDS",
    "build_signature_input",
    "compute",
    "delegated_auth_headers",
    "get_delegated_token_provider",
    "get_delegation_grant_factory",
    "get_delegation_grant_store",
    "get_endpoint_resolver",
    "grant_key",
    "is_well_formed",
    "new_grant_id",
    "new_nonce",
    "set_delegated_token_provider",
    "set_delegation_grant_factory",
    "set_delegation_grant_store",
    "set_endpoint_resolver",
    "sign",
    "verify",
]
