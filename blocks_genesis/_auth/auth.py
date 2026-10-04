import json
import logging
import asyncio
from typing import Any, Dict, List, NamedTuple, Optional, Tuple
from datetime import datetime, timezone

from fastapi import Request, HTTPException, Depends
import aiohttp
import jwt
from jwt import PyJWKClient, ExpiredSignatureError, InvalidTokenError
from cryptography import x509
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.hazmat.primitives import serialization

from blocks_genesis._auth.blocks_context import BlocksContext, BlocksContextManager
from blocks_genesis._auth.jwt_signing_algorithm import (
    is_single_key_source,
    is_symmetric,
    to_wire_names,
)
from blocks_genesis._auth.security_log import security_log
from blocks_genesis._auth.third_party_provider import ThirdPartyJwtProvider
from blocks_genesis._auth.third_party_provider_store import get_third_party_provider_store
from blocks_genesis._auth.third_party_selector import (
    ThirdPartyProviderSelection,
    select as select_third_party_provider,
)
from blocks_genesis._cache import CacheClient
from blocks_genesis._cache.cache_provider import CacheProvider
from blocks_genesis._delegation.context import AuthClaimsContext
from blocks_genesis._database.db_context import DbContext
from blocks_genesis._lmt.activity import Activity
from blocks_genesis._subscription.context import SubscriptionUsageContext
from blocks_genesis._subscription.models import UsageResult
from blocks_genesis._subscription.usage_service import SubscriptionUsageService
from blocks_genesis._tenant.tenant import Tenant
from blocks_genesis._tenant.tenant_service import TenantService
from blocks_genesis._utilities.crypto_service import CryptoService

_logger = logging.getLogger(__name__)

# Names which external identity provider a token came from, for the one case issuer
# and audience cannot separate: two providers configured with both the same.
THIRD_PARTY_IDP_HEADER = "x-blocks-idp"



# ============================================================================
# TOKEN EXTRACTION
# ============================================================================

async def extract_token_from_request(request: Request, tenant_service: TenantService) -> Tuple[Optional[str], bool, Optional[str]]:
    """
    Extract token from request.
    Returns: (token, is_third_party_token, application_domain)

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
            return token, False, BlocksContextManager.resolve_application_domain(request)

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

    # Localhost/dev fallback: support loopback equivalents when the cookie was set on a different local host alias.
    local_token = _try_localhost_alias_cookie(request, application_domain)
    if local_token:
        return local_token, False, application_domain  # Primary tenant token, not third-party

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


def _try_localhost_alias_cookie(request: Request, application_domain: Optional[str]) -> Optional[str]:
    """Return a cookie set under a loopback alias when the app domain is a localhost host."""
    if not (application_domain and BlocksContextManager.is_localhost_host(application_domain)):
        return None
    for local_host in ("localhost", "127.0.0.1", "::1"):
        if local_host == application_domain:
            continue
        token = request.cookies.get(local_host)
        if token:
            return token
    return None


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

async def _resolve_signing_tenant(
    token: str,
    tenant: Tenant,
    tenant_id: Optional[str],
    tenant_service: TenantService,
) -> Tenant:
    """
    Resolve the tenant whose signing key produced the token.

    For impersonation tokens the signature is created by the original (home)
    tenant, so the token's `original_tenant_id` must be used to load the
    verification certificate/issuer instead of the (impersonated) context
    tenant. For all other tokens this returns the provided tenant unchanged.
    """
    try:
        claims = jwt.decode(token, options={"verify_signature": False})
    except Exception:
        return tenant

    if claims.get("impersonated"):
        signer_id = claims.get("original_tenant_id")
        if signer_id and signer_id != tenant_id:
            signer_tenant = await tenant_service.get_tenant(signer_id)
            if signer_tenant:
                return signer_tenant

    return tenant


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
    4. Validate token (primary or fallback, impersonation-aware)
    5. Create and store BlocksContext with metadata
    6. Set activity properties

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

    # 4. Validate token based on source (primary or fallback, impersonation-aware)
    payload, provider = await _validate_authenticated_token(
        token, is_third_party, tenant, tenant_id, cache_client, request, tenant_service
    )

    # 5. Create and store BlocksContext
    if provider is not None:
        # The provider wrote these claims, so none of them may reach a consumer. Strip
        # them, then rebuild from the tenant record and the provider's own mapping.
        payload = BlocksContextManager.strip_reserved_claims(payload)
        mapped = map_third_party_claims(payload, provider)
        if mapped is None:
            raise HTTPException(status_code=401, detail="Token validation failed")

        try:
            blocks_context = BlocksContextManager.create_third_party_context(
                tenant_id=tenant.tenant_id,
                subject=mapped["subject"],
                organization_id=provider.default_organization_id,
                roles=mapped["roles"],
                email=mapped["email"],
                user_name=mapped["user_name"],
                display_name=mapped["display_name"],
                application_domain=application_domain or "",
                request_uri=str(request.url),
            )
        except ValueError as err:
            security_log(
                "third_party_subject_missing",
                "The UserId mapping resolved to nothing. Unlike genesis-net, which lets a "
                "bare \"_external\" principal through, this refuses the token.",
                err,
                detail={"key": provider.key, "mapping": provider.claims_mapping.user_id},
                is_warning=True,
            )
            raise HTTPException(status_code=401, detail="Token validation failed") from err

        security_log(
            "third_party_context_created",
            "Token mapped to a Blocks context; the request is authenticated.",
            detail={
                "tenantId": blocks_context.tenant_id,
                "key": provider.key,
                "userId": blocks_context.user_id,
                "organizationId": blocks_context.organization_id,
                "roles": blocks_context.roles,
                "emailResolved": bool(mapped["email"]),
                "userNameResolved": bool(mapped["user_name"]),
                "displayNameResolved": bool(mapped["display_name"]),
            },
        )
    else:
        blocks_context = BlocksContextManager.create_from_jwt_claims(
            payload,
            tenant_id,
            application_domain=application_domain or ""
        )

    BlocksContextManager.set_context(blocks_context)

    # token_version and security_stamp are not on BlocksContext, but a send needs them to build a
    # delegation grant. Stash the validated claims so it costs no extra I/O later.
    AuthClaimsContext.set(payload)

    # 6. Set activity properties for tracing
    Activity.set_current_property("baggage.UserId", blocks_context.user_id)
    Activity.set_current_property("baggage.IsAuthenticated", "true")
    # What actually validated the token, not where it arrived from: a bearer token from
    # an external provider is not flagged third-party at extraction time.
    is_external = provider is not None
    Activity.set_current_property("baggage.IsThirdPartyToken", str(is_external))

    _logger.info(
        f"User {blocks_context.user_id} authenticated for tenant {blocks_context.tenant_id} "
        f"(third_party={is_external})"
    )

    return payload


async def _validate_authenticated_token(
    token: str,
    is_third_party: bool,
    tenant: Any,
    tenant_id: Optional[str],
    cache_client: CacheClient,
    request: Request,
    tenant_service: TenantService,
) -> Tuple[Dict[str, Any], Optional[ThirdPartyJwtProvider]]:
    """Validate the token against the correct signer.

    Returns the JWT payload and the external provider that accepted it, or None for a
    Blocks token. The caller needs the provider: an external payload must be washed
    before anything reads it, and the provider carries the claim mapping and the
    organization scope that replace what was washed away.

    Raises HTTPException when validation fails.
    """
    if is_third_party:
        # Arrived in a provider cookie, so it never goes near primary validation.
        # The enable flag is checked inside the fallback.
        result = await validate_with_fallback(token, tenant, request, cache_client)
        if result is None:
            raise HTTPException(status_code=401, detail="Token validation failed")
        return result.claims, result.provider

    # A tenant that has opted in routes on the token's own issuer: one minted by a
    # configured provider is validated here, and anything else -- a Blocks token above
    # all -- falls straight through to primary validation without a wasted attempt.
    if getattr(tenant, "is_third_party_jwt_enabled", False):
        result = await validate_with_fallback(token, tenant, request, cache_client)
        if result is not None:
            return result.claims, result.provider
        # Not ours, or ours and broken. Either way primary validation gets its turn, so
        # a provider token caught mid key-rotation is not locked out by one failure.

    # Resolve the tenant whose key actually SIGNED the token. Impersonation
    # tokens are signed by the original (home) tenant and carry its id in the
    # `iss` / `original_tenant_id` claims, whereas the context tenant_id is the
    # impersonated tenant. Verifying against the impersonated tenant's cert
    # would fail signature, so always verify against the signer. Downstream
    # scoping (create_from_jwt_claims) still uses the impersonated tenant_id.
    signing_tenant = await _resolve_signing_tenant(token, tenant, tenant_id, tenant_service)

    try:
        return await validate_jwt_token(token, signing_tenant, cache_client, request), None
    except InvalidTokenError as err:
        # PyJWT's own error, not an HTTPException. Letting it escape turned a bad
        # signature into a 500, and -- before the opt-in routing above -- stopped a
        # bearer third-party token ever reaching the provider path.
        security_log(
            "authentication_failed",
            "Primary token validation failed.",
            err,
            is_warning=True,
        )
        raise HTTPException(status_code=401, detail="Token validation failed") from err

def create_certificate(certificate_data: bytes, password: Optional[str] = None):
    """Load a certificate from PKCS#12, PEM or DER bytes.

    PKCS#12 first, then a bare certificate -- the same order genesis-net uses, so a
    provider that uploads a plain .crt or .der is read too.

    Where the certificate sits inside a PKCS#12 depends on whether the bundle carries a
    private key. With one it is on `.cert`; without one `.cert` is None and it is in
    `additional_certs`. A verification certificate has no private key, so both branches
    are needed -- reading only `.cert` fails every tenant login.
    """
    if not certificate_data:
        return None

    try:
        password_bytes = password.encode('utf-8') if password else None
        bundle = pkcs12.load_pkcs12(certificate_data, password_bytes)

        # A bundle holding a private key puts its certificate on `.cert`.
        if bundle.cert is not None:
            return bundle.cert.certificate

        # A PUBLIC-ONLY bundle has no private key, so `.cert` is None and the
        # certificate arrives in `additional_certs` instead. That is the shape a
        # tenant's published verification certificate has, so this branch is the
        # ordinary case here, not an edge case.
        if bundle.additional_certs:
            return bundle.additional_certs[0].certificate
    except Exception:
        pass

    for load in (x509.load_pem_x509_certificate, x509.load_der_x509_certificate):
        try:
            return load(certificate_data)
        except Exception:
            continue

    _logger.error("Failed to create certificate from the supplied bytes.")
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
        cached = cache_client.get_bytes_value(cache_key)
        if cached:
            return cached
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
                await cache_client.add_bytes_value_async(cache_key, cert_bytes, ttl)
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
        # Reported as its own outcome: an expired token is not a misconfiguration, and
        # conflating the two sends people looking at perfectly good settings.
        security_log("token_expired", "The token is expired.")
        raise HTTPException(401, "Token expired")
    except InvalidTokenError as e:
        _logger.warning(f"Token validation failed: {e}")
        raise


# ============================================================================
# FALLBACK VALIDATION (for third-party tokens)
# ============================================================================

# How long one external provider's certificate bytes are cached.
#
# A certificate pins a single key, so this window is also how long a rotation the
# provider has already performed keeps being rejected. Short enough that the lag is an
# inconvenience rather than an outage, long enough that a busy endpoint is not
# re-fetching the file per request.
PROVIDER_CERTIFICATE_CACHE_TTL_SECONDS = 3600

# genesis-net pins ClockSkew to zero on the primary path only. The third-party path
# leaves it at the framework default, so the two runtimes accept the same tokens.
THIRD_PARTY_LEEWAY_SECONDS = 300


class ThirdPartyValidation(NamedTuple):
    claims: Dict[str, Any]
    provider: ThirdPartyJwtProvider


def _read_token_routing(token: str) -> Tuple[str, List[str]]:
    """Read iss and aud without validating anything.

    Safe because these only select a key set: validation then re-checks both against the
    chosen provider, so a forged issuer routes to a configuration whose keys cannot
    verify the signature. An unreadable token yields nothing and selects no provider.
    """
    try:
        claims = jwt.decode(token, options={"verify_signature": False})
    except Exception:
        return "", []

    audience = claims.get(BlocksContext.AUDIENCES_CLAIM) or []
    if isinstance(audience, str):
        audience = [audience]

    return claims.get(BlocksContext.ISSUER_CLAIM) or "", list(audience)


def _tenant_declared_on_request(request: Request) -> Optional[str]:
    """The tenant the request itself declares, from the header then the query.

    Never the token. For a Blocks token the claim is safe -- we signed it. For an
    external token it is not, because the claim is written outside this system:
    honouring it would let any provider name the tenant whose providers, claim mapping
    and roles get applied to its token.
    """
    for key in ("tenant_id", "x-blocks-key"):
        value = request.headers.get(key)
        if value:
            return value
    for key in ("tenant_id", "x-blocks-key"):
        value = request.query_params.get(key)
        if value:
            return value
    return None


def _decrypt_provider_secret(
    tenant: Tenant,
    provider: ThirdPartyJwtProvider,
    cipher: str,
    purpose: str,
) -> Optional[str]:
    """Decrypt one of a provider's stored ciphertexts under the tenant salt.

    Returns None on any failure, already reported. The purpose is woven into the log
    because a host is far more likely to hold a provider with a signing secret and one
    with a certificate passphrase than to have both fail at once.
    """
    if not tenant.tenant_salt:
        security_log(
            "third_party_secret_undecryptable",
            f"The tenant has no TenantSalt, which is the key material the {purpose} was "
            "encrypted under.",
            detail={"key": provider.key},
            is_warning=True,
        )
        return None

    plaintext = CryptoService.decrypt(cipher, tenant.tenant_salt)

    if not plaintext:
        # TenantSalt is load-bearing: regenerating it makes every stored secret for the
        # tenant undecryptable, and the symptom is a 401 carrying a valid token.
        security_log(
            "third_party_secret_undecryptable",
            f"The stored {purpose} did not decrypt. Either it was tampered with, or the "
            "tenant salt it was encrypted under has changed -- re-save the provider to "
            "re-encrypt it.",
            detail={"key": provider.key},
            is_warning=True,
        )
        return None

    return plaintext


async def _resolve_certificate_key(
    tenant: Tenant,
    provider: ThirdPartyJwtProvider,
    cache_client: Optional[CacheClient],
) -> Optional[bytes]:
    """Load this provider's public certificate and return its public key as PEM."""
    password = None
    if provider.public_certificate_password_cipher:
        password = _decrypt_provider_secret(
            tenant,
            provider,
            provider.public_certificate_password_cipher,
            "certificate passphrase",
        )
        if password is None:
            return None

    # Optional on purpose: without a cache the certificate is simply fetched each time,
    # which is slower but still correct. Failing the token instead would make a cache
    # outage look like a provider misconfiguration.
    cache_key = f"tpprovcert::{provider.tenant_id}::{provider.key}"
    certificate_data = None

    if cache_client is not None:
        try:
            certificate_data = cache_client.get_bytes_value(cache_key)
        except Exception:
            certificate_data = None

    if not certificate_data:
        try:
            certificate_data = await fetch_cert_bytes(provider.public_certificate_path)
        except Exception as err:
            security_log(
                "third_party_certificate_unreadable",
                "The provider's public certificate could not be read from its configured "
                "path. The path must be an absolute URL this process can fetch without "
                "credentials, or a file on this host.",
                err,
                detail={"key": provider.key},
                is_warning=True,
            )
            return None

        if certificate_data and cache_client is not None:
            try:
                await cache_client.add_bytes_value_async(
                    cache_key, certificate_data, PROVIDER_CERTIFICATE_CACHE_TTL_SECONDS
                )
            except Exception:
                pass

    certificate = create_certificate(certificate_data, password)
    if certificate is None:
        # create_certificate tries PKCS#12 first and falls back to a bare certificate,
        # so arriving here means neither worked. A wrong passphrase looks exactly like
        # this, which is worth saying outright.
        security_log(
            "third_party_certificate_unreadable",
            "The provider's certificate file was fetched but could not be parsed. A "
            "stored passphrase that does not match the file fails in exactly this way, "
            "as does a file that is not a certificate.",
            detail={"key": provider.key, "hasPassphrase": password is not None},
            is_warning=True,
        )
        return None

    return certificate.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )


async def _resolve_provider_key(
    token: str,
    tenant: Tenant,
    provider: ThirdPartyJwtProvider,
    cache_client: Optional[CacheClient],
) -> Optional[Any]:
    """The verification key for this provider, chosen by its configured algorithms.

    The token's own alg header never selects the key source. Doing so is the
    algorithm-confusion setup: take the RSA public key, use its bytes as an HMAC secret,
    sign HS256.
    """
    if not is_single_key_source(provider.algorithms):
        security_log(
            "third_party_algorithm_invalid",
            "A provider must configure at least one algorithm and they must all draw "
            "their key from the same place. A row mixing families, or left Unspecified, "
            "cannot be validated safely.",
            detail={"key": provider.key, "algorithms": [int(a) for a in provider.algorithms]},
            is_warning=True,
        )
        return None

    if is_symmetric(provider.algorithms[0]):
        if not provider.signing_secret_cipher:
            security_log(
                "third_party_key_source_missing",
                "This provider uses an HMAC algorithm but carries no signing secret.",
                detail={"key": provider.key},
                is_warning=True,
            )
            return None
        return _decrypt_provider_secret(
            tenant, provider, provider.signing_secret_cipher, "signing secret"
        )

    # A JWKS is preferred wherever the provider publishes one: it carries several keys,
    # so it survives the provider rotating one without anything here being touched.
    if provider.jwks_url:
        try:
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(
                None,
                lambda: PyJWKClient(provider.jwks_url).get_signing_key_from_jwt(token).key,
            )
        except Exception as err:
            security_log(
                "third_party_certificate_unreadable",
                "The provider's JWKS could not be read, or holds no key matching this token.",
                err,
                detail={"key": provider.key},
                is_warning=True,
            )
            return None

    if provider.public_certificate_path:
        return await _resolve_certificate_key(tenant, provider, cache_client)

    security_log(
        "third_party_key_source_missing",
        "This provider uses an asymmetric algorithm but configures neither a JwksUrl nor "
        "a PublicCertificatePath, so there is no key to verify with.",
        detail={"key": provider.key},
        is_warning=True,
    )
    return None


def _extract_claim_value(claims: Dict[str, Any], mapping: str) -> str:
    """Resolve a configured claim mapping to a single value.

    The mapping is a literal claim name first. Claim names are opaque and routinely
    contain dots -- every namespaced OIDC claim is a URI, such as
    https://myapp.example.com/user_id -- so splitting one is only correct when no claim
    by that exact name exists. The dotted form addresses a property inside a claim whose
    value is a JSON object, as in Keycloak's realm_access.roles.

    Never raises. This runs inside the fallback, where an exception turns a
    cryptographically valid token into a 401 reported as an issuer mismatch.
    """
    if not claims or not mapping:
        return ""

    if mapping in claims:
        value = claims[mapping]
        return "" if value is None else str(value)

    parts = mapping.split(".")
    if len(parts) < 2:
        return ""

    container = claims.get(parts[0])
    if container is None:
        return ""

    if isinstance(container, str):
        try:
            container = json.loads(container)
        except json.JSONDecodeError as err:
            security_log(
                "third_party_claim_invalid_json",
                "A mapping used the nested claim.property form, but the claim's value is "
                "not JSON.",
                err,
                detail={"mapping": mapping, "claim": parts[0]},
                is_warning=True,
            )
            return ""

    if isinstance(container, dict) and parts[1] in container:
        value = container[parts[1]]
        return "" if value is None else str(value)

    return ""


def _resolve_mapped_claim(claims: Dict[str, Any], field: str, mapping: str) -> str:
    """Wraps _extract_claim_value so a mapping that resolves to nothing is reported
    against the field it was configured for."""
    if not mapping or not mapping.strip():
        security_log(
            "third_party_claim_unmapped",
            f"No claim is mapped for {field}, so it will be empty.",
            detail={"field": field},
        )
        return ""

    value = _extract_claim_value(claims, mapping)

    if not value:
        security_log(
            "third_party_claim_unresolved",
            f"The {field} mapping matched no claim in the token, so it will be empty. "
            "Compare it against tokenClaims in the third_party_claim_mapping event above.",
            detail={"field": field, "mapping": mapping},
            is_warning=True,
        )

    return value


def extract_roles_from_claim(claims: Dict[str, Any], roles_mapping: str) -> List[str]:
    """Resolve the configured roles mapping to a role list.

    The mapping is tried as a literal claim name first: a JSON array claim arrives
    already decoded as a list, so a namespaced Auth0 or Okta roles claim needs no
    parsing at all. The legacy claim.property form (a claim holding a JSON object, as in
    Keycloak's realm_access.roles) remains the fallback.

    A single scalar claim yields a single role. Splitting a delimited value cannot be
    decided by inspection and needs a configured delimiter, which this mapping does not
    carry.
    """
    if not claims or not roles_mapping:
        return []

    literal = claims.get(roles_mapping)
    if isinstance(literal, str) and literal:
        return [literal]
    if isinstance(literal, (list, tuple)):
        found = [str(r) for r in literal if r]
        if found:
            return found

    parts = roles_mapping.split(".")
    if len(parts) < 2:
        return []

    container = claims.get(parts[0])
    if isinstance(container, str):
        try:
            container = json.loads(container)
        except json.JSONDecodeError:
            return []

    if not isinstance(container, dict):
        return []

    values = container.get(parts[-1])
    if not isinstance(values, (list, tuple)):
        return []

    return [v for v in values if isinstance(v, str)]


def map_third_party_claims(
    claims: Dict[str, Any], provider: ThirdPartyJwtProvider
) -> Optional[Dict[str, Any]]:
    """Map validated claims through the provider's own mapping.

    Returns None when the provider carries no mapping at all -- the token was accepted,
    but nothing can identify who sent it.
    """
    mapping = provider.claims_mapping

    if mapping is None or not mapping.is_configured():
        security_log(
            "third_party_claims_mapper_missing",
            "This provider has no claim mapping, so no Blocks context can be built.",
            detail={"key": provider.key},
            is_warning=True,
        )
        return None

    # The single most useful line when a mapping misbehaves: what was configured, next
    # to what the token actually carries.
    security_log(
        "third_party_claim_mapping",
        "Applying the provider's claim mapping to the token.",
        detail={
            "key": provider.key,
            "mapping": {
                "UserId": mapping.user_id,
                "Email": mapping.email,
                "UserName": mapping.user_name,
                "Name": mapping.name,
                "Roles": mapping.roles,
            },
            "tokenClaims": sorted(claims.keys()),
        },
    )

    standard_email = claims.get(BlocksContext.EMAIL_CLAIM) or ""

    # "sub" names the standard subject claim. Compared whole: a claim named "x.sub" is a
    # different claim.
    subject = (
        claims.get(BlocksContext.SUBJECT_CLAIM) or ""
        if mapping.user_id == "sub"
        else _resolve_mapped_claim(claims, "UserId", mapping.user_id)
    )

    email = standard_email or _resolve_mapped_claim(claims, "Email", mapping.email)

    user_name = (
        standard_email
        if mapping.user_name.lower() == "email"
        else _resolve_mapped_claim(claims, "UserName", mapping.user_name)
    )

    return {
        "subject": subject,
        "email": email,
        "user_name": user_name,
        "display_name": _resolve_mapped_claim(claims, "Name", mapping.name),
        "roles": extract_roles_from_claim(claims, mapping.roles),
    }


def _report_selection_failure(
    selection,
    issuer: str,
    audiences: List[str],
    providers: List[ThirdPartyJwtProvider],
    header_key: Optional[str],
) -> None:
    """Explain a selection miss in the terms an operator can act on: what the token
    carried, beside what is configured."""
    detail = {
        "tokenIssuer": issuer,
        "tokenAudiences": audiences,
        "headerKey": header_key,
        "candidates": selection.candidate_count,
        "configured": [
            {"Key": p.key, "Issuer": p.issuer, "Audiences": p.audiences} for p in providers
        ],
    }

    outcome = selection.outcome

    if outcome is ThirdPartyProviderSelection.NO_PROVIDERS:
        security_log(
            "third_party_no_providers_configured",
            "The tenant has third-party tokens enabled but no active provider rows.",
            detail=detail,
            is_warning=True,
        )
    elif outcome is ThirdPartyProviderSelection.ISSUER_UNMATCHED:
        # Routine: this is what every Blocks token looks like. Information, not a fault.
        security_log(
            "third_party_provider_unmatched",
            "No configured provider claims this token's issuer, so it is not a "
            "third-party token. Primary validation will handle it.",
            detail=detail,
        )
    elif outcome is ThirdPartyProviderSelection.ISSUER_ABSENT_UNMATCHED:
        # Loud, unlike an unrecognised issuer: Blocks always stamps an issuer, so a token
        # without one came from outside and someone meant it to be accepted here.
        security_log(
            "third_party_provider_unmatched",
            "This token carries no iss claim, and no provider is configured to receive "
            "tokens without one. Leave a provider's issuer blank to accept them, and give "
            "it a key -- the x-blocks-idp header is what separates two such providers.",
            detail=detail,
            is_warning=True,
        )
    elif outcome is ThirdPartyProviderSelection.AUDIENCE_UNMATCHED:
        security_log(
            "third_party_provider_unmatched",
            "A provider matches this issuer but none accepts the token's audience.",
            detail=detail,
            is_warning=True,
        )
    elif outcome is ThirdPartyProviderSelection.AMBIGUOUS_NO_HEADER:
        security_log(
            "third_party_provider_ambiguous",
            "Several providers share this issuer and audience, so the x-blocks-idp header "
            "is required to choose between them. Providers sharing an issuer should "
            "differ by audience.",
            detail=detail,
            is_warning=True,
        )
    elif outcome is ThirdPartyProviderSelection.AMBIGUOUS_HEADER_UNMATCHED:
        security_log(
            "third_party_provider_ambiguous",
            "The x-blocks-idp header named a provider that is not among the candidates "
            "for this token.",
            detail=detail,
            is_warning=True,
        )


# "No provider claims this issuer" is the everyday case on an enabled tenant -- every
# Blocks token looks exactly like that -- so it stays quiet. Logging it as a rejection
# would flood the channel and desensitise everyone to the events that matter.
_ROUTINE_MISSES = (
    ThirdPartyProviderSelection.ISSUER_UNMATCHED,
    ThirdPartyProviderSelection.NO_PROVIDERS,
)


async def validate_with_fallback(
    token: str,
    tenant: Tenant,
    request: Request,
    cache_client: Optional[CacheClient] = None,
) -> Optional[ThirdPartyValidation]:
    """Validate a token minted by an external identity provider.

    Returns the validated claims and the provider that validated them, or None. Every
    refusal is reported through the [Security] channel with its own event name, and a
    refusal that is not an everyday miss also gets a terminal line.
    """
    try:
        result, outcome = await _run_fallback(token, tenant, request, cache_client)
    except Exception as err:
        # Nothing here may escape. An exception would 500 the request instead of
        # letting primary validation have its turn.
        security_log(
            "fallback_unhandled_exception",
            "Unhandled exception during third-party validation.",
            err,
            is_warning=True,
        )
        result, outcome = None, ThirdPartyProviderSelection.SELECTED

    if result is None and outcome not in _ROUTINE_MISSES:
        # Terminal line. Without it the last thing in the log is the primary-validation
        # error, which repeats an issuer mismatch and reads as though the issuer were
        # misconfigured.
        security_log(
            "fallback_rejected",
            "Third-party token was not accepted; this request will be answered 401. "
            "The preceding fallback_* / third_party_* event is the actual reason.",
            is_warning=True,
        )

    return result


async def _run_fallback(
    token: str,
    tenant: Tenant,
    request: Request,
    cache_client: Optional[CacheClient],
) -> Tuple[Optional[ThirdPartyValidation], ThirdPartyProviderSelection]:
    """The fallback itself. Returns the result and why it ended that way."""
    if tenant is None:
        security_log(
            "fallback_missing_tenant_config",
            "The tenant could not be loaded, so its providers cannot be read.",
            is_warning=True,
        )
        return None, ThirdPartyProviderSelection.NO_PROVIDERS

    if not tenant.is_third_party_jwt_enabled:
        security_log(
            "third_party_not_enabled",
            "IsThirdPartyJwtEnabled is off for this tenant, so tokens from external "
            "providers are not accepted. Any 'issuer did not match' error above is the "
            "expected consequence, not the cause.",
            detail={"tenantId": tenant.tenant_id},
            is_warning=True,
        )
        return None, ThirdPartyProviderSelection.NO_PROVIDERS

    if not token:
        security_log(
            "fallback_no_token",
            "No token was present on the request, so there is nothing to validate.",
            is_warning=True,
        )
        return None, ThirdPartyProviderSelection.NO_PROVIDERS

    # The tenant is never taken from the token. Python resolves it from the request in
    # the middleware already, so this guards against a regression rather than a live
    # hole: a request that declares a different tenant than the one loaded is refused.
    declared = _tenant_declared_on_request(request)
    if declared and declared != tenant.tenant_id:
        security_log(
            "fallback_missing_tenant_context",
            "The tenant declared on the request is not the tenant being validated "
            "against. A third-party token is only ever validated against the tenant the "
            "request declares, never the one its own claims name.",
            detail={"declared": declared, "loaded": tenant.tenant_id},
            is_warning=True,
        )
        return None, ThirdPartyProviderSelection.NO_PROVIDERS

    store = get_third_party_provider_store()
    if store is None:
        security_log(
            "third_party_provider_store_missing",
            "The third-party provider store is not initialised in this host, so no "
            "provider can be resolved.",
            is_warning=True,
        )
        return None, ThirdPartyProviderSelection.NO_PROVIDERS

    providers = await store.get_active(tenant.tenant_id)
    issuer, audiences = _read_token_routing(token)
    header_key = request.headers.get(THIRD_PARTY_IDP_HEADER)

    selection = select_third_party_provider(providers, issuer, audiences, header_key)

    if not selection.is_selected:
        _report_selection_failure(selection, issuer, audiences, providers, header_key)
        return None, selection.outcome

    provider = selection.provider

    security_log(
        "third_party_provider_selected",
        "Provider selected for this token.",
        detail={
            "key": provider.key,
            "providerName": provider.provider_name,
            "issuer": provider.issuer,
            "algorithms": [int(a) for a in provider.algorithms],
            "candidates": selection.candidate_count,
        },
    )

    key = await _resolve_provider_key(token, tenant, provider, cache_client)
    if key is None:
        # Already reported with the specific reason.
        return None, ThirdPartyProviderSelection.SELECTED

    try:
        claims = jwt.decode(
            token,
            key=key,
            algorithms=to_wire_names(provider.algorithms),
            issuer=provider.issuer or None,
            audience=list(provider.audiences) or None,
            options={
                "verify_signature": True,
                "verify_exp": True,
                "verify_nbf": True,
                "verify_iss": bool(provider.issuer),
                "verify_aud": bool(provider.audiences),
            },
            leeway=THIRD_PARTY_LEEWAY_SECONDS,
        )
    except Exception as err:
        security_log(
            "fallback_validation_failed",
            "Third-party validation did not complete.",
            err,
            detail={"key": provider.key, "issuer": provider.issuer},
            is_warning=True,
        )
        return None, ThirdPartyProviderSelection.SELECTED

    claims[BlocksContext.REQUEST_URI_CLAIM] = str(request.url)

    security_log(
        "fallback_token_validated",
        "Third-party token signature, issuer, audience and lifetime validated.",
        detail={
            "key": provider.key,
            "providerName": provider.provider_name,
            "issuer": provider.issuer,
        },
    )

    return ThirdPartyValidation(claims, provider), ThirdPartyProviderSelection.SELECTED


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


def authorize(resource_name: str = None, bypass_authorization: bool = False):
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
    if bypass_authorization is False and (not resource_name or not resource_name.strip()):
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
            security_log(
                "forbidden",
                "Authorization failed with forbidden response.",
                detail={"resource": resource_name},
            )
            raise HTTPException(status_code=403, detail="Insufficient permissions")

        return context

    return Depends(dependency)


# ============================================================================
# SUBSCRIPTION USAGE SNAPSHOT
# ============================================================================

async def resolve_subscription_usage(
    *,
    tenant_id: Optional[str] = None,
    organization_id: Optional[str] = None,
) -> Optional[List[UsageResult]]:
    """Set SubscriptionUsageContext. Context ids win, the parameters are the fallback.

    Never raises -- missing ids or a failed read leave the snapshot None.
    """
    context = BlocksContextManager.get_context()
    if context is not None:
        tenant_id = context.tenant_id or tenant_id
        organization_id = context.organization_id or organization_id

    if not tenant_id or not organization_id:
        SubscriptionUsageContext.set(None)
        return None

    try:
        snapshot = await SubscriptionUsageService.get_usage_current(
            tenant_id=tenant_id,
            organization_id=organization_id,
        )
    except Exception:
        _logger.exception("Usage lookup failed; snapshot left None.")
        snapshot = None

    SubscriptionUsageContext.set(snapshot)
    return snapshot


def subscription_usage_snapshot(
    bypass_authorization: bool = False,
    *,
    tenant_id: Optional[str] = None,
    organization_id: Optional[str] = None,
):
    """
    Resolves SubscriptionUsageContext, the same way authorize() resolves identity.

    bypass_authorization=False (default): reuse context set by a prior authorize()
    call. bypass_authorization=True: authenticate on its own first, via
    authorize(bypass_authorization=True) -- use standalone, with no authorize()
    alongside it.

    tenant_id / organization_id: fallback ids for a request with no context. Without
    them, a missing context is still a 401.

    Reads usage straight from Mongo (no Utilities HTTP call). Read it back with
    `SubscriptionUsageContext.current()`. Never raises on a missing organization or a DB
    error -- the snapshot is just left None (fail open).
    """
    async def dependency(request: Request) -> Optional[BlocksContext]:
        if bypass_authorization:
            context = await authorize(bypass_authorization=True).dependency(request)
        else:
            context = BlocksContextManager.get_context()

        if not context and not (tenant_id and organization_id):
            raise HTTPException(status_code=401, detail="Missing context")

        await resolve_subscription_usage(
            tenant_id=tenant_id, organization_id=organization_id
        )
        return context

    return Depends(dependency)
