"""Wire-level constants for delegated access (RFC 8693 token exchange).

These values are the cross-SDK contract. blocks-genesis-net must match them byte for byte:
key names, header names, the id format, and the signature input string.
"""

# Message header (ApplicationProperties / AMQP header) carrying the opaque grant id.
DELEGATION_GRANT_HEADER = "DelegationGrant"

# Redis key prefixes.
GRANT_KEY_PREFIX = "delegation:"
NONCE_KEY_PREFIX = "nonce:"
REDEMPTION_KEY_PREFIX = "redemption:"

# Opaque grant ids are "dg_" + 64 lowercase hex chars from 32 random bytes. Never a UUID.
GRANT_ID_PREFIX = "dg_"
GRANT_ID_RANDOM_BYTES = 32
NONCE_RANDOM_BYTES = 16

TOKEN_EXCHANGE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:token-exchange"
DELEGATION_GRANT_TOKEN_TYPE = "urn:blocks:params:oauth:token-type:delegation-grant"

# Default absolute lifetime of a grant record, in seconds (2 days).
DEFAULT_GRANT_TTL_SECONDS = 2 * 24 * 60 * 60

# Nonce replay window, in seconds. At least twice the clock window.
NONCE_TTL_SECONDS = 120

# Accepted clock skew on the `ts` field, in seconds.
CLOCK_WINDOW_SECONDS = 60

# How long before a token's expiry a cached entry stops being served, in seconds.
TOKEN_RENEWAL_MARGIN_SECONDS = 60

BLOCKS_KEY_HEADER = "x-blocks-key"

# Claims carrying the version material. Not on BlocksContext; read from the validated token.
TOKEN_VERSION_CLAIM = "token_version"
SECURITY_STAMP_CLAIM = "security_stamp"

# Configuration keys. Resolution order for both is
# environment variable -> configuration root -> FrontendRuntime section.
IAM_BASE_URL_KEY = "BLOCKS_IAM_BASE_URL"
IAM_TOKEN_ENDPOINT_KEY = "BLOCKS_IAM_TOKEN_ENDPOINT"
FRONTEND_RUNTIME_SECTION = "FrontendRuntime"


def grant_key(delegation_id: str) -> str:
    return f"{GRANT_KEY_PREFIX}{delegation_id}"


def build_signature_input(tenant_id: str, delegation_id: str, nonce: str, ts: int) -> str:
    """The pipe-delimited signature input. Exact field order, no whitespace."""
    return f"{tenant_id}|{delegation_id}|{nonce}|{ts}"
