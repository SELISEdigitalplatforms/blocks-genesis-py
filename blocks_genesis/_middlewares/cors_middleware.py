from typing import Sequence

from starlette.datastructures import Headers, MutableHeaders
from starlette.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from blocks_genesis._middlewares.origin_policy import OriginPolicy


class GenesisCORSMiddleware:
    """CORS that echoes an origin, with credentials, only when OriginPolicy allows it.

    Starlette's origin check is synchronous and the tenant-domain lookup is not, so the
    decision is made here once per request and the request is then handed to one of two
    stock CORSMiddleware instances, which keep all of the header handling.
    """

    def __init__(self, app: ASGIApp, allowed_origins: Sequence[str] = ()) -> None:
        self.app = app
        self.policy = OriginPolicy(allowed_origins)
        common = dict(allow_methods=["*"], allow_headers=["*"])
        # Reached only after the policy allowed the origin: echo it (never "*") with credentials and Vary: Origin.
        self._allowed = CORSMiddleware(app, allow_origin_regex=".*", allow_credentials=True, **common)
        # Refused origin: no Access-Control-Allow-Origin and no credentials header; a preflight gets 400.
        self._refused = CORSMiddleware(app, allow_origins=(), **common)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_varying_on_origin(message: Message) -> None:
            # The CORS headers depend on Origin, so a shared cache must not reuse a response across origins.
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                vary = [v.strip().lower() for v in headers.get("vary", "").split(",")]
                if "origin" not in vary:
                    headers.add_vary_header("Origin")
            await send(message)

        origin = Headers(scope=scope).get("origin")
        if origin is None:
            await self.app(scope, receive, send_varying_on_origin)
            return
        target = self._allowed if await self.policy.is_allowed(origin) else self._refused
        await target(scope, receive, send_varying_on_origin)
