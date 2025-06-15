from fastapi import Depends, Request, HTTPException
from pymongo.collection import Collection
from datetime import datetime, timezone
import base64
import aiohttp
import asyncio
import jwt
from jwt import InvalidTokenError

from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.hazmat.primitives import serialization

from blocks_genesis.auth.blocks_context import BlocksContext, BlocksContextManager
from blocks_genesis.cache import CacheClient
from blocks_genesis.cache.cache_provider import CacheProvider
from blocks_genesis.database.db_context import DbContext
from blocks_genesis.tenant.tenant import Tenant
from blocks_genesis.tenant.tenant_service import TenantService, get_tenant_service



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
    key = f"tetocertpublic::{tenant_id}"
    cert_bytes = cache_client.get_string_value(key)
    if cert_bytes is None:
        cert_bytes = await fetch_cert_bytes(tenant.jwt_token_parameters.public_certificate_path)
        now = datetime.now(timezone.utc)
        issue_date = tenant.jwt_token_parameters.issue_date
        if issue_date.tzinfo is None:
            issue_date = issue_date.replace(tzinfo=timezone.utc)
        days_remaining = (
            tenant.jwt_token_parameters.certificate_valid_for_number_of_days
            - (now - issue_date).days
            - 1
        )
        ttl = max(60, days_remaining  * 24 * 60 * 60)  # Ensure at least 60 seconds TTL
        if ttl > 0:
            cached_value = base64.b64encode(cert_bytes).decode("utf-8")
            await cache_client.add_string_value(key, cached_value, ex=int(ttl))
    return cert_bytes


async def authenticate(request: Request, tenant_service: TenantService, cache_client: CacheClient):
    header = request.headers.get("Authorization")
    if header and any(header.startswith(prefix) for prefix in ["bearer ", "Bearer "]):
        token = header.split(" ", 1)[1].strip()
    else:
        token = request.cookies.get("access_token", "")
        
    if not token:
        raise HTTPException(401, "Token missing")

    tenant_id = BlocksContextManager.get_context().tenant_id if BlocksContextManager.get_context() else None
    if not tenant_id:
        raise HTTPException(401, "Tenant ID missing")

    tenant = await tenant_service.get_tenant(tenant_id)
    cert_bytes = await get_tenant_cert(cache_client, tenant, tenant_id)
    
    cert = create_certificate(cert_bytes, tenant.jwt_token_parameters.public_certificate_password)
    if not cert:
        raise HTTPException(500, "Failed to load certificate")
    
    public_key = cert.public_key()
    public_key_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    )
    public_key_str = public_key_pem.decode('utf-8')
    
    try:
        payload = jwt.decode(
            jwt=token,
            key=public_key_str,
            algorithms=["RS256"],
            issuer=tenant.jwt_token_parameters.issuer,
            audience=tenant.jwt_token_parameters.audiences,
            options={
                "verify_signature": True,  
                "verify_exp": True,        
                "verify_iss": True,        
                "verify_aud": True, 
                "verify_iat": True,
                "verify_nbf": True,      
                "require":["exp", "iat", "iss", "aud", "nbf"]
            },
            leeway=0 
        )
        extended_payload = dict(payload)
        extended_payload[BlocksContext.REQUEST_URI_CLAIM] = str(request.url)
        extended_payload[BlocksContext.TOKEN_CLAIM] = token

        BlocksContextManager.create_from_jwt_claims(extended_payload)
        return extended_payload
    except InvalidTokenError as e:
        print(f"JWT verification failed: {e}")
        raise HTTPException(401, f"Invalid token: {e}")

    

def create_certificate(certificate_data: bytes, password: str = None):
    """Load public certificate from PFX data"""
    try:
        password_bytes = password.encode('utf-8') if password else None
        certificate = pkcs12.load_pkcs12(certificate_data, password_bytes)
        return certificate.additional_certs[0].certificate
    except Exception as e:
        print(f"Failed to create certificate: {e}")
        return None

def authorize(bypass_authorization: bool = False):
    async def dependency(request: Request):
        tenant_service = get_tenant_service()
        cache_client = CacheProvider.get_client()
        db_context = DbContext.get_provider()

        await authenticate(request, tenant_service, cache_client)
        context = BlocksContextManager.get_context()
        if not context:
            raise HTTPException(401, "Missing context")

        if bypass_authorization:
            return

        roles = context.roles or []
        permissions = context.permissions or []

        # Parse controller and action from URL path
        path_parts = request.url.path.strip("/").split("/")
        if len(path_parts) >= 4:
            controller = path_parts[2]
            action = path_parts[3]
        elif len(path_parts) >= 2:
            controller = path_parts[0]
            action = path_parts[1]
        else:
            raise HTTPException(400, "Invalid URL format.")

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

    return Depends(dependency)
