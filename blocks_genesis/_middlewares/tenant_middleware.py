
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from datetime import datetime
from typing import AsyncIterator, Callable
from urllib.parse import urlparse
from opentelemetry.trace import StatusCode
from blocks_genesis._auth.blocks_context import BlocksContextManager
from blocks_genesis._lmt.activity import Activity
from blocks_genesis._tenant.tenant import Tenant
from blocks_genesis._tenant.tenant_service import get_tenant_service
import json
SENSITIVE_KEYS = {
    "authorization",
    "cookie",
    "set-cookie",
    "secret",
    "x-blocks-service-key",
    "x-api-key",
    "token",
    "access_token",
    "refresh_token",
    "password",
}

def is_sensitive_key(key: str) -> bool:
    if not key:
        return False
    key_lower = key.lower()
    return (
        key_lower in SENSITIVE_KEYS
        or "token" in key_lower
        or "secret" in key_lower
        or "password" in key_lower
    )

def sanitize_dict(d: dict) -> dict:
    return {k: ("[REDACTED]" if is_sensitive_key(k) else v) for k, v in d.items()}


async def tee_body_iterator(
    iterator: AsyncIterator[bytes],
    on_chunk: Callable[[int], None],
):
    async for chunk in iterator:
        on_chunk(len(chunk))
        yield chunk



class TenantValidationMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, included_paths=None):
        """
        Middleware to validate tenant only for requests whose path matches or starts with any of the included_paths.
        By default, only /api endpoints are validated. You can pass a list of custom paths to include additional endpoints.

        Example usage:
            app.add_middleware(TenantValidationMiddleware, included_paths=["/api", "/custom"])
        """
        super().__init__(app)
        # Only validate tenant for these paths (default: /api)
        self.included_paths = included_paths or ["/api"]



    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        # Only apply tenant validation if path matches or starts with any included prefix
        # Example: included_paths=["/api", "/custom"]
        #   /api/foo  -> validated
        #   /custom/bar -> validated
        #   /public    -> not validated
        if not any(path == p or path.startswith(p.rstrip("/ ") + "/") for p in self.included_paths):
            return await call_next(request)


        try:
            # Sanitize headers and query params for logging
            sanitized_headers = sanitize_dict(dict(request.headers))
            sanitized_query = sanitize_dict(dict(request.query_params))
            Activity.set_current_properties({
                "http.query": json.dumps(sanitized_query),
                "http.headers": json.dumps(sanitized_headers)
            })

            api_key = request.headers.get("x-blocks-key") or request.query_params.get("x-blocks-key") or request.query_params.get("tenant_id")
            tenant: Tenant | None = None
            tenant_service = get_tenant_service()

            if not api_key:
                request_host = request.base_url.hostname or ""
                if BlocksContextManager.is_localhost_host(request_host):
                    return self._reject(400, "BadRequest: Missing_Tenant_Key_Or_Id")

                tenant = await tenant_service.get_tenant_by_domain(request_host)
                if not tenant:
                    return self._reject(404, "Not_Found: Application_Not_Found")
            else:
                tenant = await tenant_service.get_tenant(api_key)

            if not tenant or tenant.is_disabled:
                return self._reject(404, "Not_Found: Application_Not_Found")

            if not self._is_valid_origin_or_referer(request, tenant):
                return self._reject(406, "NotAcceptable: Invalid_Origin_Or_Referer")

            Activity.set_current_property("baggage.TenantId", tenant.tenant_id)
            Activity.set_current_property(
                "baggage.IsFromCloud",
                "true" if tenant.is_root_tenant else "false"
            )

            application_domain = BlocksContextManager.resolve_application_domain(request)
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
                original_tenant_id=tenant.tenant_id,
                application_domain=application_domain or "",
                impersonated=False
            )
            BlocksContextManager.set_context(ctx)
            Activity.set_current_property("SecurityContext", str(ctx.__dict__))

            request_size = int(request.headers.get("content-length", 0))

            response = await call_next(request)

            response_size = 0

            def add_size(n: int):
                nonlocal response_size
                response_size += n

            if hasattr(response, "body_iterator") and response.body_iterator is not None:
                response.body_iterator = tee_body_iterator(
                    response.body_iterator,
                    add_size
                )

            Activity.set_current_property("request.size.bytes", request_size)
            Activity.set_current_property("response.size.bytes", response_size)
            Activity.set_current_property(
                "throughput.total.bytes",
                request_size + response_size
            )
            Activity.set_current_property("usage", True)

            if not (200 <= response.status_code < 300):
                Activity.set_current_property(
                    StatusCode.ERROR,
                    f"HTTP {response.status_code}"
                )


            sanitized_response_headers = sanitize_dict(dict(response.headers))
            Activity.set_current_properties({
                "response.status.code": response.status_code,
                "response.headers": json.dumps(sanitized_response_headers),
            })

            return response

        except Exception as e:
            Activity.set_current_status(StatusCode.ERROR, str(e))
            raise

        finally:
            BlocksContextManager.clear_context()

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
                if not url:
                    return ""
                candidate = url if "://" in url else f"//{url}"
                parsed = urlparse(candidate)
                if parsed.hostname:
                    return parsed.hostname.lower()
            except Exception:
                pass
            return BlocksContextManager.normalize_domain(url)

        allowed = [extract_domain(d) for d in (tenant.allowed_domains or []) if d]
        
        # Also add application domains from Applications array
        if tenant.applications:
            for app in tenant.applications:
                if app.domain:
                    allowed.append(extract_domain(app.domain))
        
        current = (
            extract_domain(request.headers.get("origin") or "")
            or extract_domain(request.headers.get("referer") or "")
        )

        return (
            not current
            or BlocksContextManager.is_localhost_host(current)
            or current in allowed
        )
