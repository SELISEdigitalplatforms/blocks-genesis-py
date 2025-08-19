from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from datetime import datetime
from opentelemetry.trace import StatusCode
from blocks_genesis._auth.blocks_context import BlocksContextManager
from blocks_genesis._lmt.activity import Activity
from blocks_genesis._tenant.tenant import Tenant
from blocks_genesis._tenant.tenant_service import get_tenant_service


class TenantValidationMiddleware(BaseHTTPMiddleware):

    async def dispatch(self, request: Request, call_next):
        excluded_paths = ["/ping", "/swagger/index.html", "/openapi.json"]
        root_path = request.scope.get("root_path", "")

        all_excluded = excluded_paths + [root_path + path for path in excluded_paths]

        if request.url.path in all_excluded:
            return await call_next(request)
        
        try:
            Activity.set_current_properties({
                "http.query": str(dict(request.query_params)),
                "http.headers": str(dict(request.headers))
            })
            
            
            api_key = request.headers.get("x-blocks-key") or request.query_params.get("x-blocks-key")
            tenant: Tenant = None
            tenant_service = get_tenant_service()  # Assuming this function retrieves the tenant service instance

            if not api_key:
                tenant = await tenant_service.get_tenant_by_domain(request.base_url.hostname)
                if not tenant:
                    return self._reject(404, "Not_Found: Application_Not_Found")
            else:
                tenant = await tenant_service.get_tenant(api_key)

            if not tenant or tenant.is_disabled:
                return self._reject(404, "Not_Found: Application_Not_Found")

            if not self._is_valid_origin_or_referer(request, tenant):
                return self._reject(406, "NotAcceptable: Invalid_Origin_Or_Referer")
            

            Activity.set_current_property("baggage.TenantId", tenant.tenant_id)
            Activity.set_current_property("baggage.IsFromCloud", "true" if tenant.is_root_tenant else "false")
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
                oauth_token="",
                actual_tenant_id=tenant.tenant_id
            )
            BlocksContextManager.set_context(ctx)
            Activity.set_current_property("SecurityContext", str(ctx.__dict__))
            
            request_size = int(request.headers.get("content-length", 0))

            response = await call_next(request)
            
            body = b"".join([chunk async for chunk in response.body_iterator])
            response_size = len(body)

            Activity.set_current_property("request.size.bytes", request_size)
            Activity.set_current_property("response.size.bytes", response_size)
            Activity.set_current_property("throughput.total.bytes", request_size + response_size)
            
            response = Response(
                content=body,
                status_code=response.status_code,
                headers=dict(response.headers),
                media_type=response.media_type
            )
            
            if not (200 <= response.status_code < 300):
                Activity.set_current_property(StatusCode.ERROR, f"HTTP {response.status_code}")
            Activity.set_current_properties({
                "response.status.code": response.status_code,
                "response.headers": str(dict(response.headers)),
            })
        
        except Exception as e:
            Activity.set_current_status(StatusCode.ERROR, str(e))
            raise
        finally:
            BlocksContextManager.clear_context()

        return response           
    
     
            
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

        allowed = [extract_domain(d) for d in tenant.allowed_domains]
        current = extract_domain(request.headers.get("origin") or "") or extract_domain(request.headers.get("referer") or "")
        normalized_app_domain = extract_domain(tenant.application_domain)

        return not current or current == "localhost" or current == normalized_app_domain or current in allowed
