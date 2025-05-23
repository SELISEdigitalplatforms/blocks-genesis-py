from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
import uuid

class TraceMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request.state.trace_id = str(uuid.uuid4())
        response = await call_next(request)
        response.headers["X-Trace-Id"] = request.state.trace_id
        return response
