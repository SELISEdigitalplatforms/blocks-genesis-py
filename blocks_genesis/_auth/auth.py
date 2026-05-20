import json
import logging
import base64
import asyncio
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime, timezone

from fastapi import Request, HTTPException, Depends
import aiohttp
import jwt
from jwt import PyJWKClient, ExpiredSignatureError, InvalidTokenError
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.hazmat.primitives import serialization

from blocks_genesis._auth.blocks_context import BlocksContext, BlocksContextManager
from blocks_genesis._cache import CacheClient
from blocks_genesis._cache.cache_provider import CacheProvider
from blocks_genesis._database.db_context import DbContext
from blocks_genesis._lmt.activity import Activity
from blocks_genesis._tenant.tenant import Tenant
from blocks_genesis._tenant.tenant_service import TenantService

_logger = logging.getLogger(__name__)



# ============================================================================
# TOKEN EXTRACTION
# ============================================================================

async def extract_token_from_request(request: Request, tenant_service: TenantService) -> Tuple[Optional[str], bool]:
    """
    Extract token from request.
    Returns: (token, is_third_party_token)
    
    Extraction priority:
    1. Authorization: Bearer <token> header
    2. Tenant-specific cookie (using application domain)
    3. Third-party provider cookie (from tenant config)
    
    The is_third_party_token flag indicates whether the token came from
    third-party JWT parameters configuration.
    """
    # 1. Check Authorization header (primary source)
    auth_header = request.headers.get("Authorization") or ""
    if auth_header.lower().startswith("bearer "):
        token = auth_header[7:].strip()  # Remove "Bearer " prefix
        if token:
            return token, False
    
    # 2. Fall back to cookies
    return await _extract_token_from_cookie(request, tenant_service)


async def _extract_token_from_cookie(request: Request, tenant_service: TenantService) -> Tuple[Optional[str], bool, Optional[str]]:
    """
    Extract token from cookies.
    
    1. Tries tenant-specific cookie using application domain
    2. Falls back to third-party token cookie from tenant config
    
    Returns: (token, is_third_party_token, application_domain)
    """
    # Validate BlocksContext exists with tenant_id
    context = BlocksContextManager.get_context()
    if not context or not context.tenant_id:
        return None, False, None
    
    # Resolve application domain from request headers (Origin > Referer > Host)
    application_domain = BlocksContextManager.resolve_application_domain(request)

    # Localhost/dev fallback: support loopback equivalents when cookie was set on a different local host alias.
    if application_domain and BlocksContextManager.is_localhost_host(application_domain):
        for local_host in ("localhost", "127.0.0.1", "::1"):
            if local_host == application_domain:
                continue
            token = request.cookies.get(local_host)
            if token:
                return token, False, application_domain  # Primary tenant token, not third-party
            
    # 1. Try tenant-specific cookie using application domain as name
    if application_domain:
        token = request.cookies.get(application_domain)
        if token:
            return token, False, application_domain  # Primary tenant token, not third-party
    
    # 2. Fall back to third-party token cookie
    tenant = await tenant_service.get_tenant(context.tenant_id)
    if not tenant or not tenant.third_party_jwt_token_parameters:
        return None, False, None
    
    cookie_key = tenant.third_party_jwt_token_parameters.cookie_key
    if not cookie_key:
        return None, False, None
    
    third_party_token = request.cookies.get(cookie_key)
    if third_party_token:
        return third_party_token, True, application_domain  # Third-party token from provider
    
    return None, False, None


# ============================================================================
# CERTIFICATE HANDLING
# ============================================================================

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


# ============================================================================
# MAIN AUTHENTICATION HANDLER
# ============================================================================

async def authenticate(
    request: Request,
    tenant_service: TenantService,
    cache_client: Optional[CacheClient] = None
) -> Dict[str, Any]:
    """
    Main authentication handler.
    
    Flow:
    1. Extract token from Authorization header or cookies
    2. Resolve tenant ID (from claims or context)
    3. Load tenant configuration
    4. Determine if token is third-party (based on tenant config)
    5. Validate token (primary or fallback)
    6. Create and store BlocksContext with metadata
    7. Set activity properties
    
    Raises HTTPException on authentication failure.
    """
    if cache_client is None:
        cache_client = CacheProvider.get_client()
    
    # 1. Extract token (returns token and whether it's from third-party provider)
    token, is_third_party, application_domain = await extract_token_from_request(request, tenant_service)
    if not token:
        raise HTTPException(status_code=401, detail="Token missing")
    
    # 2. Resolve tenant ID from context
    context = BlocksContextManager.get_context()
    tenant_id = context.tenant_id if context else None

    if not tenant_id:
        tenant_id = request.headers.get("x-blocks-key") or request.query_params.get("x-blocks-key") or request.query_params.get("tenant_id")
    
    # 3. Load tenant configuration
    tenant = await tenant_service.get_tenant(tenant_id) if tenant_id else None
    if not tenant:
        raise HTTPException(status_code=401, detail="Tenant not found")
    
    # Store tenant in request state for later use
    request.state._blocks_tenant = tenant
    
    # 4. Validate token based on source
    payload = None
    
    if is_third_party:
        # Third-party token: use fallback validation (JWKS or public cert)
        payload = await validate_with_fallback(token, tenant, request)
        if not payload:
            raise HTTPException(status_code=401, detail="Token validation failed")
    else:
        # Tenant token: try primary validation first, then fallback
        try:
            payload = await validate_jwt_token(token, tenant, cache_client, request)
        except HTTPException:
            # If primary fails, try fallback validation
            _logger.info("Primary validation failed, attempting fallback")
            payload = await validate_with_fallback(token, tenant, request)
            if not payload:
                raise HTTPException(status_code=401, detail="Token validation failed")
    
    # 5. Create and store BlocksContext
    blocks_context = BlocksContextManager.create_from_jwt_claims(
        payload,
        tenant_id,
        application_domain=application_domain or ""
    )
    BlocksContextManager.set_context(blocks_context)
    
    # 6. Set activity properties for tracing
    Activity.set_current_property("baggage.UserId", blocks_context.user_id)
    Activity.set_current_property("baggage.IsAuthenticated", "true")
    Activity.set_current_property("baggage.IsThirdPartyToken", str(is_third_party))
    
    _logger.info(
        f"User {blocks_context.user_id} authenticated for tenant {blocks_context.tenant_id} "
        f"(third_party={is_third_party})"
    )
    
    return payload

def create_certificate(certificate_data: bytes, password: Optional[str] = None):
    """Load certificate from PKCS12 data."""
    try:
        password_bytes = password.encode('utf-8') if password else None
        cert = pkcs12.load_pkcs12(certificate_data, password_bytes)
        return cert.additional_certs[0].certificate if cert.additional_certs else None
    except Exception as e:
        _logger.error(f"Failed to create certificate: {e}")
        return None


async def get_tenant_cert(cache_client: CacheClient, tenant: Tenant, tenant_id: str) -> Optional[bytes]:
    """
    Get tenant's public certificate from cache or fetch and cache it.
    Caches based on certificate validity period.
    """
    if not tenant.jwt_token_parameters:
        return None
    
    cache_key = f"tetocertpublic::{tenant_id}"
    
    # Try cache first
    try:
        cached = cache_client.get_string_value(cache_key)
        if cached:
            return base64.b64decode(cached)
    except Exception:
        pass
    
    # Fetch certificate
    cert_bytes = await fetch_cert_bytes(tenant.jwt_token_parameters.public_certificate_path)
    if not cert_bytes:
        return None
    
    # Calculate TTL based on certificate validity
    try:
        now = datetime.now(timezone.utc)
        issue_date = tenant.jwt_token_parameters.issue_date
        if issue_date and issue_date.tzinfo is None:
            issue_date = issue_date.replace(tzinfo=timezone.utc)
        
        if issue_date:
            days_remaining = (
                tenant.jwt_token_parameters.certificate_valid_for_number_of_days
                - (now - issue_date).days - 1
            )
            ttl = max(60, days_remaining * 86400)  # At least 60 seconds
            
            if ttl > 0:
                cached_value = base64.b64encode(cert_bytes).decode("utf-8")
                await cache_client.add_string_value_async(cache_key, cached_value, ttl)
    except Exception as e:
        _logger.warning(f"Failed to cache certificate: {e}")
    
    return cert_bytes


# ============================================================================
# JWT VALIDATION (Primary)
# ============================================================================

async def validate_jwt_token(
    token: str,
    tenant: Tenant,
    cache_client: CacheClient,
    request: Request
) -> Dict[str, Any]:
    """
    Validate JWT token using tenant's public certificate.
    Returns decoded payload if valid.
    """
    if not tenant.jwt_token_parameters:
        raise HTTPException(401, "Invalid tenant configuration")
    
    # Get certificate
    cert_bytes = await get_tenant_cert(cache_client, tenant, tenant.tenant_id)
    if not cert_bytes:
        raise HTTPException(500, "Failed to load certificate")
    
    # Create certificate and extract public key
    cert = create_certificate(cert_bytes, tenant.jwt_token_parameters.public_certificate_password)
    if not cert:
        raise HTTPException(500, "Failed to load certificate")
    
    public_key = cert.public_key()
    public_key_pem = public_key.public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode('utf-8')
    
    # Decode and validate
    try:
        payload = jwt.decode(
            token,
            key=public_key_pem,
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
                "require": ["exp", "iat", "iss", "aud", "nbf"]
            },
            leeway=0
        )
        
        # Enrich payload with request metadata
        payload[BlocksContext.REQUEST_URI_CLAIM] = str(request.url)
        payload[BlocksContext.TOKEN_CLAIM] = token
        
        return payload
    
    except ExpiredSignatureError:
        _logger.warning("Token expired")
        raise HTTPException(401, "Token expired")
    except InvalidTokenError as e:
        _logger.warning(f"Token validation failed: {e}")
        raise


# ============================================================================
# FALLBACK VALIDATION (for third-party tokens)
# ============================================================================

async def _validate_via_jwks(token: str, jwks_url: str, issuer: str, audiences: List[str]) -> Optional[Dict[str, Any]]:
    """Validate token using JWKS endpoint."""
    try:
        loop = asyncio.get_running_loop()
        
        def _get_key():
            client = PyJWKClient(jwks_url)
            signing_key = client.get_signing_key_from_jwt(token)
            return signing_key.key
        
        public_key = await loop.run_in_executor(None, _get_key)
        
        payload = jwt.decode(
            token,
            key=public_key,
            algorithms=["RS256", "RS384", "RS512"],
            issuer=issuer or None,
            audience=audiences or None,
            options={"verify_signature": True, "verify_exp": True, "verify_nbf": True},
            leeway=0
        )
        _logger.info("Token validated via JWKS")
        return payload
    except Exception as e:
        _logger.warning(f"JWKS validation failed: {e}")
        return None


async def _validate_via_public_cert(
    token: str,
    cert_path: str,
    cert_password: Optional[str],
    issuer: str,
    audiences: List[str]
) -> Optional[Dict[str, Any]]:
    """Validate token using public certificate."""
    try:
        cert_bytes = await fetch_cert_bytes(cert_path)
        if not cert_bytes:
            return None
        
        cert = create_certificate(cert_bytes, cert_password)
        if not cert:
            return None
        
        public_key = cert.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        )
        
        payload = jwt.decode(
            token,
            key=public_key,
            algorithms=["RS256", "RS384", "RS512"],
            issuer=issuer or None,
            audience=audiences or None,
            options={"verify_signature": True, "verify_exp": True, "verify_nbf": True},
            leeway=0
        )
        _logger.info("Token validated via public certificate")
        return payload
    except Exception as e:
        _logger.warning(f"Public certificate validation failed: {e}")
        return None


async def validate_with_fallback(
    token: str,
    tenant: Tenant,
    request: Request
) -> Optional[Dict[str, Any]]:
    """
    Try to validate third-party token using JWKS or public certificate.
    Returns payload if valid, None otherwise.
    """
    if not tenant.third_party_jwt_token_parameters:
        return None
    
    params = tenant.third_party_jwt_token_parameters
    
    # Try JWKS first if available
    if params.jwks_url:
        payload = await _validate_via_jwks(
            token,
            params.jwks_url,
            params.issuer,
            params.audiences
        )
        if payload:
            payload[BlocksContext.REQUEST_URI_CLAIM] = str(request.url)
            payload[BlocksContext.TOKEN_CLAIM] = token
            return payload
    
    # Fall back to public certificate
    if params.public_certificate_path:
        payload = await _validate_via_public_cert(
            token,
            params.public_certificate_path,
            params.public_certificate_password,
            params.issuer,
            params.audiences
        )
        if payload:
            payload[BlocksContext.REQUEST_URI_CLAIM] = str(request.url)
            payload[BlocksContext.TOKEN_CLAIM] = token
            return payload
    
    return None


async def check_standard_access(
    context: BlocksContext,
    resource_name: str,
    db_context: DbContext
) -> bool:
    """
    Check if user has access to protected resource.
    
    Validates:
    1. Resource name is provided (mandatory)
    2. User is within quota limits
    3. User has required roles or permissions
    
    Returns True if access is allowed, False otherwise.
    """
    if not resource_name:
        _logger.warning("Resource name is required for protected endpoint access")
        return False
    
    if not context or not context.tenant_id:
        _logger.warning("Tenant context required for access check")
        return False
    
    # Check rate limit quota
    if not await _check_quota(context, resource_name, db_context):
        _logger.warning(f"Rate limit exceeded for resource {resource_name}")
        return False
    
    # Check permissions
    roles = context.roles or []
    permissions = context.permissions or []
    
    has_access = await _check_permission(resource_name, roles, permissions, context.original_tenant_id if context.impersonated else context.tenant_id, db_context)
    return has_access


async def _check_quota(
    context: BlocksContext,
    resource_name: str,
    db_context: DbContext
) -> bool:
    """
    Check if user is within rate limit quota for resource.
    
    Returns False if limit exceeded (429), True otherwise.
    """
    try:
        return True  # Quota check is currently disabled, always allow access. Implement actual logic as needed.
        collection = await db_context.get_collection("ResourceLimits", tenant_id=context.tenant_id)
        
        resource_limit = await collection.find_one({"Resource": resource_name})
        if not resource_limit:
            return True  # No limit configured, allow access
        
        limit = resource_limit.get("Limit", 0)
        usage = resource_limit.get("Usage", 0)
        
        remaining = limit - usage
        if remaining <= 0:
            _logger.warning(f"Quota exceeded for {resource_name}: limit={limit}, usage={usage}")
            return False
        
        return True
    except Exception as e:
        _logger.error(f"Error checking quota for {resource_name}: {e}")
        return True  # Allow on error


async def _check_permission(
    resource_name: str,
    roles: List[str],
    permissions: List[str],
    tenant_id: str,
    db_context: DbContext
) -> bool:
    """
    Check if user has permission to access resource.
    
    Checks both role-based and permission-based access.
    Returns True if user has any matching role or permission.
    """
    try:
        # Determine tenant_id from context, handling impersonation
        bc = BlocksContextManager.get_context()
        effective_tenant_id = None
        if bc is not None:
            effective_tenant_id = bc.original_tenant_id if getattr(bc, 'impersonated', False) and getattr(bc, 'original_tenant_id', None) else bc.tenant_id
        if not effective_tenant_id:
            effective_tenant_id = tenant_id
        if not effective_tenant_id:
            return False

        collection = await db_context.get_collection("Permissions", tenant_id=effective_tenant_id)
        organization_id = getattr(bc, 'organization_id', None) if bc else None
        if not organization_id or not str(organization_id).strip():
            organization_id = "default"

        # If no roles and no permissions, deny access
        if not roles and not permissions:
            _logger.warning(
                f"Access denied for resource {resource_name}: "
                f"user has no roles or permissions"
            )
            return False

        # AND: OrganizationId == organization_id
        # OR:
        #   - Resource in permissions
        #   - (Resource == resource_name AND Roles in roles)
        or_conditions = []
        if permissions:
            or_conditions.append({"Resource": {"$in": permissions}})
        if roles:
            or_conditions.append({
                "$and": [
                    {"Resource": resource_name},
                    {"Roles": {"$in": roles}}
                ]
            })
        query = {
            "OrganizationId": organization_id,
            "$or": or_conditions
        }

        count = await collection.count_documents(query)
        has_access = count > 0

        if not has_access:
            _logger.warning(
                f"Access denied for resource {resource_name}: "
                f"roles={roles}, permissions={permissions}"
            )

        return has_access
    except Exception as e:
        _logger.error(f"Error checking permissions for {resource_name}: {e}")
        return False


def authorize(resource_name: str = "", bypass_authorization: bool = False):
    """
    FastAPI dependency for authorization with mandatory resource protection.

    Args:
        resource_name: The protected resource name. Required for protected endpoints
            (i.e. when bypass_authorization is False); optional when authorization
            is bypassed since it is never used in that path.
        bypass_authorization: If True, skips authorization checks while still authenticating.

    Returns:
        FastAPI Depends that authenticates and authorizes the request.

    Raises:
        ValueError: If resource_name is missing/empty on a protected (non-bypassed) endpoint.
    """
    if not bypass_authorization and (not resource_name or not resource_name.strip()):
        raise ValueError("resource_name is required for protected endpoint authorization")
    
    async def dependency(request: Request):
        tenant_service = TenantService()
        cache_client = CacheProvider.get_client()
        db_context = DbContext.get_provider()

        # 1. Authenticate
        await authenticate(request, tenant_service, cache_client)

        context = BlocksContextManager.get_context()
        if not context:
            raise HTTPException(status_code=401, detail="Missing context")

        # 2. Bypass authorization if requested
        if bypass_authorization:
            return context

        # 3. Check resource access with mandatory resource_name
        # (matches .NET ProtectedEndpointAccessHandler logic)
        has_access = await check_standard_access(
            context=context,
            resource_name=resource_name,
            db_context=db_context
        )
        
        if not has_access:
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        
        return context

    return Depends(dependency)
