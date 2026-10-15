"""Parse a received message's `SecurityContext` header, shared by both workers.

It only parses; the worker sets the context. A present header that cannot be parsed is invalid and
the message must not be handled. Neither the header nor the parse error is ever logged, because a
validation error echoes the input values.
"""

import json
from typing import Optional, Tuple

from blocks_genesis._auth.blocks_context import BlocksContext, BlocksContextManager

# The Azure Service Bus dead-letter reason; the reason code below goes in its description.
INVALID_SECURITY_CONTEXT_DEAD_LETTER_REASON = "InvalidSecurityContext"
SECURITY_CONTEXT_INVALID = "security_context_invalid"


def parse_security_context_header(raw: Optional[str]) -> Tuple[bool, Optional[BlocksContext]]:
    """(valid, context). An absent or empty header is valid with no context."""
    if not raw:
        return True, None
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            return False, None
        return True, BlocksContextManager.from_transport(payload)
    except (ValueError, TypeError):  # JSONDecodeError and pydantic's ValidationError are ValueErrors
        return False, None
