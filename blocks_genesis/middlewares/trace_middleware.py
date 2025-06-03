from fastapi import Request

from blocks_genesis.auth.blocks_context import BlocksContextManager
from blocks_genesis.lmt.activity import Activity


class TraceContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):

        Activity.set_current_property("Request.Url", str(request.url.path))
        Activity.set_current_property("Request.Method", request.method)
        Activity.set_current_property("Request.Headers", str(dict(request.headers)))
        Activity.set_current_property("Request.Host", request.client.host)
        Activity.set_current_property("Request.Query", str(dict(request.query_params)))
        Activity.set_current_property("Request.Protocol", request.client.protocol)
        

        response = await call_next(request)

        Activity.set_current_property("Response.StatusCode", response.status_code)
        Activity.set_current_property("Response.Headers", str(dict(response.headers)))

        BlocksContextManager.clear_context()
        return response