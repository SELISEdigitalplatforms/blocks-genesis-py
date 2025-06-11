from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from datetime import datetime
from blocks_genesis.auth.blocks_context import BlocksContextManager
from blocks_genesis.lmt.activity import Activity
from blocks_genesis.tenant.tenant import Tenant
from blocks_genesis.tenant.tenant_service import get_tenant_service


class TenantValidationMiddleware(BaseHTTPMiddleware):

    async def dispatch(self, request: Request, call_next):
        api_key = request.headers.get("x-blocks-key") or request.query_params.get("x-blocks-key")
        tenant: Tenant = None
        tenant_service = get_tenant_service()  # Assuming this function retrieves the tenant service instance

        if not api_key:
            domain = str(request.url.hostname)
            tenant = await tenant_service.get_tenant_by_domain(domain)
            if not tenant:
                return self._reject(404, "Not_Found: Application_Not_Found")
        else:
            tenant = await tenant_service.get_tenant(api_key)

        if not tenant or tenant.is_disabled:
            return self._reject(404, "Not_Found: Application_Not_Found")

        if not self._is_valid_origin_or_referer(request, tenant):
            return self._reject(406, "NotAcceptable: Invalid_Origin_Or_Referer")
        

        Activity.set_baggage_item("TenantId", tenant.tenant_id)
        print(f"TenantId set in baggage: {tenant.tenant_id}")
        # Construct and set BlocksContext
        ctx = BlocksContextManager.create(
            tenant_id=tenant.tenant_id,
            roles=[],
            user_id="",
            is_authenticated=False,
            request_uri=request.url.path,
            organization_id="",
            expire_on=datetime.now(),
            email="",
            permissions=[],
            user_name="",
            phone_number="",
            display_name="",
            oauth_token=""
        )
        BlocksContextManager.set_context(ctx)

        # Set span-level attributes
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

    def _is_valid_origin_or_referer(self, request: Request, tenant: Tenant) -> bool:
        def extract_domain(url: str) -> str:
            try:
                return url.split("//")[-1].split("/")[0].split(":")[0]
            except:
                return ""

        allowed = tenant.allowed_domains
        current = extract_domain(request.headers.get("origin") or "") or extract_domain(request.headers.get("referer") or "")

        return not current or current == "localhost" or current == tenant.application_domain or current in allowed
