"""The signature scheme protecting a token exchange.

Kept in its own module because it is a cross-SDK contract: blocks-genesis-net and blocks-iam
must produce and verify the same bytes for the same inputs.
"""

import hashlib
import hmac
import secrets

from blocks_genesis._delegation.constants import (
    GRANT_ID_PREFIX,
    GRANT_ID_RANDOM_BYTES,
    NONCE_RANDOM_BYTES,
    build_signature_input,
)

_LOWER_HEX = set("0123456789abcdef")


def new_grant_id() -> str:
    """`dg_` + 64 lowercase hex chars from 32 cryptographically random bytes."""
    return f"{GRANT_ID_PREFIX}{secrets.token_hex(GRANT_ID_RANDOM_BYTES)}"


def new_nonce() -> str:
    """A single-use exchange nonce: 16 cryptographically random bytes, lowercase hex."""
    return secrets.token_hex(NONCE_RANDOM_BYTES)


def is_well_formed(delegation_id: str | None) -> bool:
    """True only for `dg_` followed by exactly 64 lowercase hex characters."""
    if not delegation_id or not delegation_id.startswith(GRANT_ID_PREFIX):
        return False

    body = delegation_id[len(GRANT_ID_PREFIX):]
    if len(body) != GRANT_ID_RANDOM_BYTES * 2:
        return False

    return all(char in _LOWER_HEX for char in body)


def compute(signature_input: str, tenant_salt: str) -> str:
    """HMAC-SHA256 over the input, keyed by the tenant salt (UTF-8). Lowercase hex."""
    return hmac.new(
        tenant_salt.encode("utf-8"),
        signature_input.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def sign(tenant_id: str, delegation_id: str, nonce: str, ts: int, tenant_salt: str) -> str:
    """Convenience wrapper building the input from its parts, then signing it."""
    return compute(build_signature_input(tenant_id, delegation_id, nonce, ts), tenant_salt)


def verify(expected: str, presented: str | None) -> bool:
    """Constant-time comparison of two hex signatures."""
    if not presented:
        return False
    return hmac.compare_digest(expected, presented)
