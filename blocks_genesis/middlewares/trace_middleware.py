from fastapi import Request

from blocks_genesis.auth.blocks_context import BlocksContextManager
from blocks_genesis.lmt.activity import Activity


class TraceContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):

        Activity.set_current_property("Request.Url", str(request.url.path))
        Activity.set_current_property("Request.Method", request.method)

        response = await call_next(request)

        Activity.set_current_property("Response.StatusCode", response.status_code)

        BlocksContextManager.clear_context()
        return response