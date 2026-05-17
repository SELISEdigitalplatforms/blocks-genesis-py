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
    SERVICE_ACCESS_CLAIM: ClassVar[str] = "service_access"
    ORGANIZATION_ID_CLAIM: ClassVar[str] = "org_id"
    EMAIL_CLAIM: ClassVar[str] = "email"
    USER_NAME_CLAIM: ClassVar[str] = "user_name"
    DISPLAY_NAME_CLAIM: ClassVar[str] = "name"
    PHONE_NUMBER_CLAIM: ClassVar[str] = "phone"
    IMPERSONATED_CLAIM: ClassVar[str] = "impersonated"
    ACTUAL_TENANT_ID_CLAIM: ClassVar[str] = "actual_tenant_id"
    ACTOR_USER_CLAIM: ClassVar[str] = "actor_user"
    
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
    actual_tenant_id: str = ""
    application_domain: str = ""  # Domain extracted from Origin/Referer headers
    impersonated: bool = False
    actor_user: Optional[str] = None  # For impersonation scenarios

    
    class Config:
        arbitrary_types_allowed = True

# Context variables for async context management
_context_var: ContextVar[Optional[BlocksContext]] = ContextVar('blocks_context', default=None)
_test_mode = threading.local()

class BlocksContextManager:
    """Manages BlocksContext instances and provides utility methods"""
    
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
    def create_from_jwt_claims(claims: Dict[str, Any], actual_tenant_id: str = "", application_domain: str = "") -> BlocksContext:
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

        if not actual_tenant_id:
            actual_tenant_id: str = get_claim_value(BlocksContext.ACTUAL_TENANT_ID_CLAIM)

        if not actual_tenant_id:
            actual_tenant_id = get_claim_value(BlocksContext.TENANT_ID_CLAIM)
        
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
            actual_tenant_id=actual_tenant_id,
            application_domain=application_domain,
            impersonated=get_claim_value(BlocksContext.IMPERSONATED_CLAIM, False),
            actor_user=get_claim_value(BlocksContext.ACTOR_USER_CLAIM, None)
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
        actual_tenant_id: Optional[str] = None,
        application_domain: str = "",
        impersonated: bool = False,
        actor_user: Optional[str] = None
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
            actual_tenant_id=actual_tenant_id or "",
            application_domain=application_domain,
            impersonated=impersonated,
            actor_user=actor_user
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
    
    

