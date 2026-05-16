"""
Tenant context resolution helper utilities.

Equivalent to .NET TenantContextHelper.cs - resolves tenant context from requests
with priority-based tenant ID and domain resolution.
"""

import json
import logging
from typing import Optional, Tuple
from datetime import datetime

import jwt
from fastapi import Request

from blocks_genesis._auth.blocks_context import BlocksContext, BlocksContextManager
from blocks_genesis._core.configuration import get_config

_logger = logging.getLogger(__name__)

TENANT_ID_PARAM_NAME = "tenant_id"
BLOCKS_KEY = "blocks"
TENANT_RESOLUTION_KEYS = [TENANT_ID_PARAM_NAME, BLOCKS_KEY]


async def resolve_tenant_id_async(request: Request, token: Optional[str] = None) -> Optional[str]:
    """
    Resolve tenant ID from request in priority order:
    
    1. Headers (tenant_id, blocks)
    2. Query parameters (tenant_id, blocks)
    3. Form data (tenant_id, blocks)
    4. JWT token claims (tenant_id)
    
    Matches .NET TenantContextHelper.ResolveTenantIdAsync()
    
    Args:
        request: FastAPI request object
        token: Optional JWT token (if already extracted)
        
    Returns:
        Tenant ID if found, None otherwise
    """
    # Try headers first
    tenant_id = _resolve_tenant_id_from_headers(request)
    if tenant_id:
        return tenant_id
    
    # Try query parameters
    tenant_id = _resolve_tenant_id_from_query(request)
    if tenant_id:
        return tenant_id
    
    # Try form data
    try:
        # Check if request has form content type
        if request.headers.get("content-type", "").startswith("application/x-www-form-urlencoded"):
            form_data = await request.form()
            tenant_id = _resolve_tenant_id_from_form(form_data)
            if tenant_id:
                return tenant_id
    except Exception as e:
        _logger.debug(f"Failed to parse form data: {e}")
    
    # Try JWT token claims
    if token:
        tenant_id = _resolve_tenant_id_from_token(token)
        if tenant_id:
            return tenant_id
    
    return None


def _resolve_tenant_id_from_headers(request: Request) -> Optional[str]:
    """Extract tenant ID from request headers."""
    for key in TENANT_RESOLUTION_KEYS:
        value = request.headers.get(key)
        if value and value.strip():
            return value.strip()
    return None


def _resolve_tenant_id_from_query(request: Request) -> Optional[str]:
    """Extract tenant ID from query parameters."""
    for key in TENANT_RESOLUTION_KEYS:
        value = request.query_params.get(key)
        if value and value.strip():
            return value.strip()
    return None


def _resolve_tenant_id_from_form(form_data) -> Optional[str]:
    """Extract tenant ID from form data."""
    for key in TENANT_RESOLUTION_KEYS:
        value = form_data.get(key)
        if value and str(value).strip():
            return str(value).strip()
    return None


def _resolve_tenant_id_from_token(token: str) -> Optional[str]:
    """Extract tenant ID from JWT token claims without validation."""
    try:
        # Decode without verification to get claims
        decoded = jwt.decode(token, options={"verify_signature": False})
        tenant_id = decoded.get(BlocksContext.TENANT_ID_CLAIM)
        return tenant_id if tenant_id and str(tenant_id).strip() else None
    except Exception as e:
        _logger.debug(f"Failed to extract tenant ID from token: {e}")
        return None


def ensure_tenant_context(request: Request, tenant_id: Optional[str]) -> None:
    """
    Ensure BlocksContext is set with tenant ID if not already set.
    
    Creates a minimal unauthenticated context with just tenant_id if needed.
    
    Matches .NET TenantContextHelper.EnsureTenantContext()
    
    Args:
        request: FastAPI request object
        tenant_id: Tenant ID to set in context
    """
    if not tenant_id or not str(tenant_id).strip():
        return
    
    existing_context = BlocksContextManager.get_context()
    if existing_context and existing_context.tenant_id and \
       existing_context.tenant_id.lower() == str(tenant_id).lower():
        return
    
    # Create minimal unauthenticated context with tenant_id
    domain = BlocksContextManager.resolve_application_domain(request)
    
    seeded_context = BlocksContext(
        tenant_id=tenant_id,
        roles=[],
        user_id="",
        is_authenticated=False,
        request_uri=request.headers.get("Host", ""),
        organization_id="",
        expire_on=None,
        email="",
        permissions=[],
        user_name="",
        phone_number="",
        display_name="",
        oauth_token="",
        actual_tenant_id=tenant_id,
        application_domain=domain or "",
    )
    
    BlocksContextManager.set_context(seeded_context)


def resolve_application_domain(url: str) -> str:
    """
    Normalize URLs to domain names (removes protocol, port, path).
    
    Example: "https://app.example.com:8080/path" → "app.example.com"
    
    Matches .NET BlocksContext.NormalizeDomain()
    
    Args:
        url: Full URL to normalize
        
    Returns:
        Normalized domain name
    """
    if not url or not str(url).strip():
        return ""
    
    # Remove protocols
    normalized = str(url).strip()
    normalized = normalized.replace("https://", "").replace("http://", "")
    
    # Extract domain (before / and :)
    domain = normalized.split("/")[0].split(":")[0].strip()
    
    return domain
