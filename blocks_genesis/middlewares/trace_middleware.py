from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from blocks_genesis.auth.blocks_context import BlocksContextManager
from blocks_genesis.lmt.activity import Activity


class TraceContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        # Start a root span/activity
        with Activity(f"HTTP {request.method} {request.url.path}") as activity:
            activity.set_properties({
                "Request.Url": str(request.url),
                "Request.Path": request.url.path,
                "Request.Method": request.method,
                "Request.Host": request.client.host if request.client else "",
                "Request.Query": str(dict(request.query_params)),
                "Request.Headers": str(dict(request.headers)),
                "Request.Protocol": request.scope.get("http_version", "unknown"),
                "Request.Scheme": request.url.scheme,
                "Request.Client": f"{request.client.host}:{request.client.port}" if request.client else "",
            })

            try:
                response = await call_next(request)
            except Exception as e:
                activity.set_status(Activity.StatusCode.ERROR, str(e))
                activity.add_event("exception", {
                    "exception.type": type(e).__name__,
                    "exception.message": str(e),
                })
                raise

            activity.set_properties({
                "Response.StatusCode": response.status_code,
                "Response.Headers": str(dict(response.headers)),
            })

            return response

        # Context is auto-detached and cleaned up here
        BlocksContextManager.clear_context()
