from contextvars import ContextVar
from datetime import datetime
from typing import ClassVar, List, Optional, Dict, Any
from pydantic import BaseModel, Field
import threading

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
    def create_from_jwt_claims(claims: Dict[str, Any]) -> BlocksContext:
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
            oauth_token=get_claim_value(BlocksContext.TOKEN_CLAIM)
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
        oauth_token: Optional[str] = None
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
            oauth_token=oauth_token or ""
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
    
    

