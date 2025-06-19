import logging
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from blocks_genesis._lmt.activity import Activity

_logger = logging.getLogger(__name__)

class GlobalExceptionHandlerMiddleware(BaseHTTPMiddleware):
    JSON_CONTENT_TYPE = "application/json"
    EMPTY_JSON_BODY = "Empty"
    MAX_PAYLOAD_LENGTH = 1000


    async def dispatch(self, request: Request, call_next):
        try:
            return await call_next(request)
        except Exception as exc:
            return await self.handle_exception(request, exc)

    async def handle_exception(self, request: Request, exception: Exception):
        payload = self.EMPTY_JSON_BODY

        if request.headers.get("content-type", "").startswith(self.JSON_CONTENT_TYPE):
            try:
                body = await request.body()
                payload = body.decode("utf-8").strip()
                if not payload:
                    payload = self.EMPTY_JSON_BODY
                elif len(payload) > self.MAX_PAYLOAD_LENGTH:
                    payload = payload[:self.MAX_PAYLOAD_LENGTH] + "... [truncated]"
            except Exception:
                payload = "[Unable to read request body]"


        trace_id = Activity.get_trace_id()

        url = str(request.url)
        method = request.method
        message = (
            f"Unhandled exception thrown on request Trace: [{trace_id}] "
            f"Method: [{method}] {url} : {str(exception)}.\n"
            f"Payload: {payload}"
        )
        _logger.exception(message)

        # Return standardized JSON error
        response = {
            "Message": "An error occurred while processing your request.",
            "TraceId": trace_id
        }

        return JSONResponse(content=response, status_code=500)
