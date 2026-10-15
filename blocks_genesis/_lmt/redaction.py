"""One redaction rule for everything written to logs and traces."""

import hashlib
import re

from blocks_genesis._delegation.constants import GRANT_KEY_PREFIX, NONCE_KEY_PREFIX, REDEMPTION_KEY_PREFIX

REDACTED = "[REDACTED]"

SENSITIVE_KEYS = frozenset({
    "authorization", "proxy-authorization", "cookie", "set-cookie",
    "x-blocks-service-key", "x-api-key", "x-blocks-key",
    "access_token", "refresh_token", "code", "sig",
})

# Matched inside the key after lower-casing and dropping "-" and "_".
_SENSITIVE_KEY_PARTS = ("token", "secret", "password", "apikey", "signature",
                        "session", "jwt", "credential")

_JWT = re.compile(r"eyJ[A-Za-z0-9_-]*\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]*")
_GRANT_ID = re.compile(r"dg_[0-9a-fA-F]{64}")

# Set by the worker from the message's own headers, never from the Baggage header.
_RESERVED_BAGGAGE_KEYS = frozenset({"tenantid", "securitycontext"})

_HASHED_KEY_PREFIXES = (GRANT_KEY_PREFIX, NONCE_KEY_PREFIX, REDEMPTION_KEY_PREFIX)


def is_sensitive_key(key) -> bool:
    if not key:
        return False
    lowered = str(key).lower()
    compact = lowered.replace("-", "").replace("_", "")
    return lowered in SENSITIVE_KEYS or any(part in compact for part in _SENSITIVE_KEY_PARTS)


def redact_value(value):
    if not isinstance(value, str):
        return value
    return _GRANT_ID.sub(REDACTED, _JWT.sub(REDACTED, value))


def sanitize_dict(d: dict) -> dict:
    return {k: (REDACTED if is_sensitive_key(k) else redact_value(v)) for k, v in d.items()}


def safe_baggage_items(baggages: dict) -> dict:
    """Baggage from a message header, as string span attributes without the reserved keys."""
    return {
        key: REDACTED if is_sensitive_key(key) else redact_value(str(value))
        for key, value in baggages.items()
        if str(key).lower() not in _RESERVED_BAGGAGE_KEYS
    }


def redact_cache_key(key):
    """Grant, nonce and redemption keys keep their prefix and a short stable hash of the rest."""
    if isinstance(key, str):
        for prefix in _HASHED_KEY_PREFIXES:
            if key.startswith(prefix):
                digest = hashlib.sha256(key[len(prefix):].encode("utf-8")).hexdigest()[:12]
                return f"{prefix}sha256:{digest}"
    return redact_value(key)
