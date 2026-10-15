import logging
from fastapi import HTTPException
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from blocks_genesis._lmt.activity import Activity

_logger = logging.getLogger(__name__)

class GlobalExceptionHandlerMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        try:
            return await call_next(request)
        except HTTPException:
            raise
        except Exception as exc:
            return await self.handle_exception(request, exc)

    async def handle_exception(self, request: Request, exception: Exception):
        # Neither the body nor the query string is logged: either can carry credentials or personal data.
        trace_id = Activity.get_trace_id()
        _logger.exception(
            "Unhandled exception thrown on request Trace: [%s] Method: [%s] %s : %s: %s",
            trace_id, request.method, request.url.path, type(exception).__name__, exception,
        )

        # Return standardized JSON error
        response = {
            "Message": "An error occurred while processing your request.",
            "TraceId": trace_id
        }

        return JSONResponse(content=response, status_code=500)
