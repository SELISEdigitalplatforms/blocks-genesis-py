# middlewares.py
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
import uuid
from datetime import datetime
from blocks_genesis.auth.blocks_context import BlocksContext, BlocksContextManager
from blocks_genesis.lmt.activity import Activity


class TenantValidationMiddleware(BaseHTTPMiddleware):

    async def dispatch(self, request: Request, call_next):
        api_key = request.headers.get("x-blocks-key") or request.query_params.get("x-blocks-key")
        tenant = None

        if not api_key:
            domain = str(request.url.hostname)
            tenant = get_tenant_by_domain(domain)
            if not tenant:
                return self._reject(404, "Not_Found: Application_Not_Found")
        else:
            tenant = get_tenant_by_id(api_key)

        if not tenant or tenant.get("is_disabled"):
            return self._reject(404, "Not_Found: Application_Not_Found")

        if not self._is_valid_origin_or_referer(request, tenant):
            return self._reject(406, "NotAcceptable: Invalid_Origin_Or_Referer")

        Activity.set_current_property("Tenant.Id", tenant["tenant_id"])
        ctx = BlocksContext(
            tenant_id=tenant["tenant_id"],
            roles=[],
            subject="",
            is_service=False,
            application_domain=tenant["application_domain"],
            username="",
            expires_at=datetime.now(),
            correlation_id=str(uuid.uuid4()),
            permissions=[],
            request_id=str(uuid.uuid4()),
            user_agent=request.headers.get("User-Agent", ""),
            ip_address=request.client.host if request.client else "",
            device_id=""
        )
        BlocksContextManager.set_context(ctx)
        Activity.set_current_property("SecurityContext", str(ctx.__dict__))

        return await call_next(request)

    def _reject(self, status: int, message: str) -> Response:
        return JSONResponse(
            status_code=status,
            content={
                "is_success": False,
                "errors": {"message": message}
            }
        )

    def _is_valid_origin_or_referer(self, request: Request, tenant: dict) -> bool:
        def extract_domain(url: str) -> str:
            try:
                return url.split("//")[-1].split("/")[0].split(":")[0]
            except:
                return ""

        allowed = set(domain.lower() for domain in tenant.get("allowed_domains", []))
        current = extract_domain(request.headers.get("origin") or "") or extract_domain(request.headers.get("referer") or "")

        return not current or current == "localhost" or current == tenant.get("application_domain") or current in allowed
