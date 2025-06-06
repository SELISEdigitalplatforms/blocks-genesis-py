from datetime import datetime, timedelta
from functools import wraps
from pymongo.collection import Collection
import aiohttp 
import asyncio
from fastapi import Request, HTTPException
import jwt
from jwt import InvalidTokenError
from cryptography import x509
from cryptography.hazmat.backends import default_backend
import base64
from blocks_genesis.auth.blocks_context import BlocksContext, BlocksContextManager
from blocks_genesis.cache import CacheClient
from blocks_genesis.cache.cache_provider import CacheProvider
from blocks_genesis.database.db_context import DbContext
from blocks_genesis.tenant.tenant import Tenant
from blocks_genesis.tenant.tenant_service import TenantService, get_tenant_service


async def authenticate(request: Request, tenant_service: TenantService, cache_client: CacheClient):
    # Check Authorization header
    header = request.headers.get("Authorization")
    if header and any(header.startswith(prefix) for prefix in ["bearer ", "Bearer "]):
        token = header.split(" ", 1)[1].strip()
    else:
        # Check cookies
        token = request.cookies.get("access_token", "")
        
    if not token:
        raise HTTPException(401, "Token missing")

    tenant_id = BlocksContextManager.get_context().tenant_id if BlocksContextManager.get_context() else None
    if not tenant_id:
        raise HTTPException(401, "Tenant ID missing")

    tenant = await tenant_service.get_tenant(tenant_id)
    cert_bytes = await get_tenant_cert(cache_client, tenant, tenant_id)
    cert = x509.load_pem_x509_certificate(cert_bytes, default_backend())
    public_key = cert.public_key()
    try:
        payload = jwt.decode(
            token,
            public_key,
            algorithms=["RS256"],
            issuer=tenant.issuer,
            audience=tenant.audiences,
        )
    except InvalidTokenError as e:
        raise HTTPException(401, f"Invalid token: {e}")

    extended_payload = dict(payload)
    extended_payload[BlocksContext.REQUEST_URI_CLAIM] = str(request.url)
    extended_payload[BlocksContext.TOKEN_CLAIM] = token

    BlocksContext.create_from_jwt_claims(extended_payload)
    return extended_payload


def authorize(bypass_authorization: bool = False):
    """ Decorator to authorize access to a controller/action based on roles and permissions.
    If `bypass_auth` is True, it skips the authorization check.
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, request: Request, **kwargs):
            tenant_service = get_tenant_service()
            cache_client = CacheProvider.get_client()
            db_context = DbContext.get_provider()

            # Authenticate & populate context
            await authenticate(request, tenant_service, cache_client)
            context = BlocksContextManager.get_context()
            if not context:
                raise HTTPException(401, "Missing context")
            
            if bypass_authorization:
                return await func(*args, request=request, **kwargs)

            roles = context.roles or []
            permissions = context.permissions or []

            # Extract controller/action from path
            path_parts = request.url.path.strip("/").split("/")
            if len(path_parts) >= 4:
                # Regular format: http://host_url/kube_service_name/version/controller/action
                controller = path_parts[2]
                action = path_parts[3]
            elif len(path_parts) >= 2:
                # Local format: http://host_url/controller/action
                controller = path_parts[0]
                action = path_parts[1]
            else:
                raise HTTPException(400, "Invalid URL format. Expected either: http://host_url/kube_service_name/version/controller/action or http://host_url/controller/action")

            resource = f"{context.service_name}::{controller}::{action}".lower()

            collection: Collection = await db_context.get_collection("Permissions", tenant_id=context.tenant_id)

            query = {
                "Type": 1,
                "Resource": resource,
                "$or": [
                    {"Roles": {"$in": roles}},
                    {"Name": {"$in": permissions}}
                ]
            }

            count = await collection.count_documents(query)
            if count < 1:
                raise HTTPException(403, "Insufficient permissions")

            return await func(*args, request=request, **kwargs)
        return wrapper
    return decorator


async def fetch_cert_bytes(cert_url: str) -> bytes:
    if cert_url.startswith("http"):
        async with aiohttp.ClientSession() as session:
            async with session.get(cert_url) as resp:
                resp.raise_for_status()
                return await resp.read()
    else:
        loop = asyncio.get_running_loop()
        try:
            with open(cert_url, "rb") as f:
                return await loop.run_in_executor(None, f.read)
        except Exception as e:
            raise RuntimeError(f"Error reading cert file {cert_url}: {e}")


async def get_tenant_cert(cache_client: CacheClient, tenant: Tenant, tenant_id: str) -> bytes:
    key = f"tenant_cert:{tenant_id}"
    cached = cache_client.get_string_value(key)
    if cached:
        cert_bytes = base64.b64decode(cached)
    else:
        cert_bytes = await fetch_cert_bytes(tenant.jwt_token_parameters.public_certificate_path)
        expired = tenant.issue_date + timedelta(days=tenant.valid_days) - timedelta(days=1)
        ttl = max(60, int((expired - datetime.now()).total_seconds()))
        if ttl > 0:
            cached_value = base64.b64encode(cert_bytes).decode("utf-8")
            await cache_client.add_string_value(key, cached_value, ex=int(ttl))
    return cert_bytes