from datetime import datetime, timedelta
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
from blocks_genesis.tenant.tenant import Tenant
from blocks_genesis.tenant.tenant_service import TenantService, get_tenant_service


async def authenticate(request: Request, tenant_service: TenantService, cache_client: CacheClient):
    header = request.headers.get("Authorization")
    if not header or not any(header.startswith(prefix) for prefix in ["bearer ", "Bearer "]):
        raise HTTPException(401, "Token missing")
    token = header[len("Bearer "):].strip()

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


def authorize(resources: list[str] = []):
    def decorator(func):
        async def wrapper(*args, request: Request, **kwargs):
            tenant_service = get_tenant_service()
            cache_client = CacheProvider.get_client()
            await authenticate(request, tenant_service, cache_client)
            payload = BlocksContextManager.get_context()
            roles = payload.roles or []
            perms = payload.permissions or []
            
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
        ttl = (expired - datetime.now()).total_seconds()
        if ttl > 0:
            cached_value = base64.b64encode(cert_bytes).decode("utf-8")
            await cache_client.add_string_value(key, cached_value, ex=int(ttl))
    return cert_bytes