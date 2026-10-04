from contextvars import ContextVar
from datetime import datetime
from typing import ClassVar, List, Optional, Dict, Any
from pydantic import BaseModel, Field
import threading
from urllib.parse import urlparse

class BlocksContext(BaseModel):
    # JWT Standard Claims
    ISSUER_CLAIM: ClassVar[str] = "iss"
    AUDIENCES_CLAIM: ClassVar[str] = "aud"
    ISSUED_AT_TIME_CLAIM: ClassVar[str] = "iat"
    NOT_BEFORE_THAT_CLAIM: ClassVar[str] = "nbf"
    EXPIRE_ON_CLAIM: ClassVar[str] = "exp"
    SUBJECT_CLAIM: ClassVar[str] = "sub"
    
    # Custom Claims
    TENANT_ID_CLAIM: ClassVar[str] = "tenant_id"
    ROLES_CLAIM: ClassVar[str] = "roles"
    USER_ID_CLAIM: ClassVar[str] = "user_id"
    REQUEST_URI_CLAIM: ClassVar[str] = "request_uri"
    TOKEN_CLAIM: ClassVar[str] = "oauth"
    PERMISSION_CLAIM: ClassVar[str] = "permissions"
    ORGANIZATION_ID_CLAIM: ClassVar[str] = "org_id"
    EMAIL_CLAIM: ClassVar[str] = "email"
    USER_NAME_CLAIM: ClassVar[str] = "user_name"
    DISPLAY_NAME_CLAIM: ClassVar[str] = "name"
    PHONE_NUMBER_CLAIM: ClassVar[str] = "phone"
    IMPERSONATED_CLAIM: ClassVar[str] = "impersonated"
    IMPERSONATION_SESSION_ID_CLAIM: ClassVar[str] = "impersonation_session_id"
    ORIGINAL_TENANT_ID_CLAIM: ClassVar[str] = "original_tenant_id"
    CLIENT_ID_CLAIM: ClassVar[str] = "client_id"

    # The IdP session this token was minted under. Standard OIDC claim. Empty for tokens
    # with no user session, such as client_credentials and token exchange.
    SESSION_ID_CLAIM: ClassVar[str] = "sid"
    
    # Properties
    tenant_id: str = ""
    roles: List[str] = Field(default_factory=list)
    user_id: str = ""
    expire_on: Optional[datetime] = None
    request_uri: str = ""
    oauth_token: str = ""
    organization_id: str = ""
    is_authenticated: bool = False
    email: str = ""
    permissions: List[str] = Field(default_factory=list)
    user_name: str = ""
    phone_number: str = ""
    display_name: str = ""
    original_tenant_id: str = ""
    application_domain: str = ""  # Domain extracted from Origin/Referer headers
    impersonated: bool = False
    impersonation_session_id: str = ""
    client_id: str = ""
    session_id: str = ""

    class Config:
        arbitrary_types_allowed = True

# Context variables for async context management
_context_var: ContextVar[Optional[BlocksContext]] = ContextVar('blocks_context', default=None)
_test_mode = threading.local()

# Widest organization scope there is, not a narrow one: consumers read it as
# tenant-wide. Narrow it deliberately for any provider that should not have that.
DEFAULT_ORGANIZATION = "default"


class BlocksContextManager:
    """Manages BlocksContext instances and provides utility methods"""

    # Claims an external identity provider must never be able to set. Stripped before
    # anything downstream sees the token, then written back from what we decided --
    # otherwise a provider names its own tenant, roles or permissions simply by minting
    # those claims into its token.
    RESERVED_CLAIMS: ClassVar[tuple] = (
        BlocksContext.TENANT_ID_CLAIM,
        BlocksContext.ORIGINAL_TENANT_ID_CLAIM,
        BlocksContext.USER_ID_CLAIM,
        BlocksContext.USER_NAME_CLAIM,
        BlocksContext.DISPLAY_NAME_CLAIM,
        BlocksContext.EMAIL_CLAIM,
        BlocksContext.PERMISSION_CLAIM,
        BlocksContext.ORGANIZATION_ID_CLAIM,
        BlocksContext.IMPERSONATED_CLAIM,
        BlocksContext.IMPERSONATION_SESSION_ID_CLAIM,
        BlocksContext.CLIENT_ID_CLAIM,
        BlocksContext.ROLES_CLAIM,
    )

    # Wire name -> context field, for the payload a message or gRPC hop carries. The
    # names are .NET's, from BlocksContext.CreateSanitizedForTransport: a worker rebuilds
    # the context from this payload and never sees the JWT, so a field left out here is
    # lost to every async consumer.
    _TRANSPORT_FIELDS: ClassVar[tuple] = (
        ("TenantId", "tenant_id"),
        ("Roles", "roles"),
        ("UserId", "user_id"),
        ("IsAuthenticated", "is_authenticated"),
        ("RequestUri", "request_uri"),
        ("OrganizationId", "organization_id"),
        ("ExpireOn", "expire_on"),
        ("Email", "email"),
        ("Permissions", "permissions"),
        ("UserName", "user_name"),
        ("PhoneNumber", "phone_number"),
        ("DisplayName", "display_name"),
        ("OauthToken", "oauth_token"),
        ("OriginalTenantId", "original_tenant_id"),
        ("ApplicationDomain", "application_domain"),
        ("Impersonated", "impersonated"),
        ("ClientId", "client_id"),
        ("SessionId", "session_id"),
    )

    @staticmethod
    def create_sanitized_for_transport(context: Optional[BlocksContext]) -> Dict[str, Any]:
        """The context as it travels on a message, matching .NET key for key.

        Email and phone number are masked, and the token never travels at all.
        ImpersonationSessionId is deliberately left out, exactly as .NET leaves it out.
        """
        if context is None:
            return {}

        email = context.email or ""
        masked_email = "***" if "@" not in email else f"***@{email.split('@')[1]}"

        phone = context.phone_number or ""
        masked_phone = "***" if not phone or phone == "***" else f"***{phone[-4:]}"

        expire_on = context.expire_on.isoformat() if context.expire_on else None

        return {
            "TenantId": context.tenant_id or "",
            "Roles": list(context.roles or []),
            "UserId": context.user_id or "",
            "IsAuthenticated": context.is_authenticated,
            "RequestUri": context.request_uri or "",
            "OrganizationId": context.organization_id or "",
            "ExpireOn": expire_on,
            "Email": masked_email,
            "Permissions": list(context.permissions or []),
            "UserName": context.user_name or "",
            "PhoneNumber": masked_phone,
            "DisplayName": context.display_name or "",
            "OauthToken": "",
            "OriginalTenantId": context.original_tenant_id or context.tenant_id or "",
            "ApplicationDomain": context.application_domain or "",
            "Impersonated": context.impersonated,
            "ClientId": context.client_id or "",
            "SessionId": context.session_id or "",
        }

    @staticmethod
    def from_transport(payload: Optional[Dict[str, Any]]) -> Optional[BlocksContext]:
        """Rebuild a context from a message payload.

        Reads .NET's names, and snake_case too so messages queued before this shipped
        still land. Unknown keys are ignored rather than raising -- a payload from a
        newer sender must not cost a worker its whole context.
        """
        if not payload:
            return None

        values = {}
        for wire_name, field in BlocksContextManager._TRANSPORT_FIELDS:
            if wire_name in payload:
                values[field] = payload[wire_name]
            elif field in payload:
                values[field] = payload[field]

        return BlocksContext(**{k: v for k, v in values.items() if v is not None})

    @staticmethod
    def strip_reserved_claims(claims: Dict[str, Any]) -> Dict[str, Any]:
        """Drop every claim an external provider must not be able to set."""
        reserved = BlocksContextManager.RESERVED_CLAIMS
        return {k: v for k, v in claims.items() if k not in reserved}

    @staticmethod
    def create_third_party_context(
        tenant_id: str,
        subject: str,
        organization_id: str = DEFAULT_ORGANIZATION,
        *,
        roles: Optional[List[str]] = None,
        email: str = "",
        user_name: str = "",
        display_name: str = "",
        application_domain: str = "",
        request_uri: str = "",
        expire_on: Optional[datetime] = None,
    ) -> BlocksContext:
        """Build the context for a validated external token.

        Takes no claims dictionary on purpose. Every value here is either resolved from
        the tenant record or from the provider's configured claim mapping, so nothing the
        external provider wrote can reach a consumer.

        Unlike .NET, a subject that did not resolve raises instead of producing a bare
        `_external` principal shared by every broken mapping.
        """
        if not tenant_id or not tenant_id.strip():
            raise ValueError("A third-party context needs the tenant from the tenant record.")
        if not subject or not subject.strip():
            raise ValueError(
                "The UserId mapping resolved to nothing, so this token identifies nobody."
            )

        return BlocksContext(
            tenant_id=tenant_id,
            original_tenant_id=tenant_id,
            user_id=f"{subject}_external",
            roles=roles or [],
            permissions=[],
            organization_id=organization_id or DEFAULT_ORGANIZATION,
            is_authenticated=True,
            email=email,
            user_name=user_name,
            display_name=display_name,
            phone_number="",
            oauth_token="",
            request_uri=request_uri,
            application_domain=application_domain,
            expire_on=expire_on,
        )

    @staticmethod
    def get_test_mode() -> bool:
        """Get test mode status (thread-safe)"""
        return getattr(_test_mode, 'value', False)
    
    @staticmethod
    def set_test_mode(value: bool) -> None:
        """Set test mode status (thread-safe)"""
        _test_mode.value = value

    @staticmethod
    def normalize_domain(url: str) -> str:
        """Normalize URL/host to hostname only (no protocol, port, or path)."""
        if not url or not str(url).strip():
            return ""

        raw = str(url).strip()
        candidate = raw if "://" in raw else f"//{raw}"

        try:
            parsed = urlparse(candidate)
            if parsed.hostname:
                return parsed.hostname.strip().lower()
        except Exception:
            pass

        return raw.replace("https://", "").replace("http://", "").split("/")[0].split(":")[0].strip().lower()

    @staticmethod
    def is_localhost_host(host: Optional[str]) -> bool:
        """Check whether host points to local development loopback addresses."""
        normalized = BlocksContextManager.normalize_domain(host or "")
        return normalized in {"localhost", "127.0.0.1", "::1"}
    
    @staticmethod
    def resolve_application_domain(request) -> Optional[str]:
        """
        Resolve application domain from request headers.
        
        Priority order:
        1. Origin header
        2. Referer header
        3. Host header
        
        Returns domain without protocol (e.g., "example.com")
        """
        # Try Origin header first (CORS)
        origin = request.headers.get("Origin")
        if origin:
            normalized = BlocksContextManager.normalize_domain(origin)
            if normalized:
                return normalized
        
        # Try Referer header
        referer = request.headers.get("Referer")
        if referer:
            normalized = BlocksContextManager.normalize_domain(referer)
            if normalized:
                return normalized
        
        return None
    
    @staticmethod
    def create_from_jwt_claims(claims: Dict[str, Any], original_tenant_id: str = "", application_domain: str = "") -> BlocksContext:
        """Create BlocksContext from JWT claims dictionary"""
        
        def get_claim_value(claim_name: str, default: Any = "") -> Any:
            return claims.get(claim_name, default)
        
        def get_claim_list(claim_name: str) -> List[str]:
            value = claims.get(claim_name, [])
            if isinstance(value, str):
                return [value]
            return value if isinstance(value, list) else []
        
        expire_on = None
        if exp_claim := claims.get(BlocksContext.EXPIRE_ON_CLAIM):
            try:
                if isinstance(exp_claim, (int, float)):
                    expire_on = datetime.fromtimestamp(exp_claim)
                elif isinstance(exp_claim, str):
                    expire_on = datetime.fromisoformat(exp_claim.replace('Z', '+00:00'))
            except (ValueError, TypeError):
                expire_on = None

        if not original_tenant_id:
            original_tenant_id: str = get_claim_value(BlocksContext.ORIGINAL_TENANT_ID_CLAIM)

        if not original_tenant_id:
            original_tenant_id = get_claim_value(BlocksContext.TENANT_ID_CLAIM)
        
        return BlocksContext(
            tenant_id=get_claim_value(BlocksContext.TENANT_ID_CLAIM),
            roles=get_claim_list(BlocksContext.ROLES_CLAIM),
            user_id=get_claim_value(BlocksContext.USER_ID_CLAIM),
            is_authenticated=True,
            request_uri=get_claim_value(BlocksContext.REQUEST_URI_CLAIM),
            organization_id=get_claim_value(BlocksContext.ORGANIZATION_ID_CLAIM),
            expire_on=expire_on,
            email=get_claim_value(BlocksContext.EMAIL_CLAIM),
            permissions=get_claim_list(BlocksContext.PERMISSION_CLAIM),
            user_name=get_claim_value(BlocksContext.USER_NAME_CLAIM),
            phone_number=get_claim_value(BlocksContext.PHONE_NUMBER_CLAIM),
            display_name=get_claim_value(BlocksContext.DISPLAY_NAME_CLAIM),
            oauth_token=get_claim_value(BlocksContext.TOKEN_CLAIM),
            original_tenant_id=original_tenant_id,
            application_domain=application_domain,
            impersonated=get_claim_value(BlocksContext.IMPERSONATED_CLAIM, False),
            impersonation_session_id=get_claim_value(BlocksContext.IMPERSONATION_SESSION_ID_CLAIM),
            client_id=get_claim_value(BlocksContext.CLIENT_ID_CLAIM),
            session_id=get_claim_value(BlocksContext.SESSION_ID_CLAIM)
        )
    
    @staticmethod
    def create(
        tenant_id: Optional[str] = None,
        roles: Optional[List[str]] = None,
        user_id: Optional[str] = None,
        is_authenticated: bool = False,
        request_uri: Optional[str] = None,
        organization_id: Optional[str] = None,
        expire_on: Optional[datetime] = None,
        email: Optional[str] = None,
        permissions: Optional[List[str]] = None,
        user_name: Optional[str] = None,
        phone_number: Optional[str] = None,
        display_name: Optional[str] = None,
        oauth_token: Optional[str] = None,
        original_tenant_id: Optional[str] = None,
        application_domain: str = "",
        impersonated: bool = False,
        impersonation_session_id: Optional[str] = None,
        client_id: Optional[str] = None,
        session_id: Optional[str] = None
    ) -> BlocksContext:
        """Create BlocksContext from individual parameters"""
        return BlocksContext(
            tenant_id=tenant_id or "",
            roles=roles or [],
            user_id=user_id or "",
            is_authenticated=is_authenticated,
            request_uri=request_uri or "",
            organization_id=organization_id or "",
            expire_on=expire_on,
            email=email or "",
            permissions=permissions or [],
            user_name=user_name or "",
            phone_number=phone_number or "",
            display_name=display_name or "",
            oauth_token=oauth_token or "",
            original_tenant_id=original_tenant_id or "",
            application_domain=application_domain,
            impersonated=impersonated,
            impersonation_session_id=impersonation_session_id or "",
            client_id=client_id or "",
            session_id=session_id or ""
        )
    
    @staticmethod
    def get_context(test_value: Optional[BlocksContext] = None) -> Optional[BlocksContext]:
        """Get the current BlocksContext"""
        try:
            # For testing scenarios
            if BlocksContextManager.get_test_mode():
                return test_value or _context_var.get()
            
            return _context_var.get()
        except Exception:
            return None
    
    @staticmethod
    def set_context(context: Optional[BlocksContext]) -> None:
        """Set the context in ContextVar storage"""
        _context_var.set(context)
    
    @staticmethod
    def clear_context() -> None:
        """Clear the current context"""
        _context_var.set(None)
    
    

