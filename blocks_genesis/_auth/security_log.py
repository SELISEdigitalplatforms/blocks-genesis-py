"""One channel for every authentication diagnostic.

Ports genesis-net `JwtBearerAuthenticationExtension.SecurityLog`. Outcomes used to be
split across separate prefixes, so grepping the obvious one showed a failure starting and
never why it ended. One grep for `[Security]` now returns the whole story of a request.

Never pass a raw connection string, a decrypted secret, a certificate passphrase, an
email address, a phone number, or a whole token as `detail`.
"""
import json
import logging
from typing import Any, Optional

_logger = logging.getLogger(__name__)


def security_log(
    event_name: str,
    message: str,
    exc: Optional[BaseException] = None,
    detail: Optional[Any] = None,
    is_warning: bool = False,
) -> None:
    payload = {
        "category": "auth",
        "eventName": event_name,
        "message": message,
        "exceptionType": type(exc).__name__ if exc else None,
        "exceptionMessage": str(exc) if exc else None,
        "detail": detail,
    }
    line = json.dumps(payload, default=str)

    if is_warning:
        _logger.warning("[Security] %s", line, exc_info=exc)
    else:
        _logger.info("[Security] %s", line)
