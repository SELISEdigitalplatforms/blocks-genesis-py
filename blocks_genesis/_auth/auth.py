import json
from typing import Any, Dict, List, Optional
from fastapi import Depends, Request, HTTPException
from pymongo.collection import Collection
from datetime import datetime, timezone
import base64
import aiohttp
import asyncio
import jwt
from jwt import ExpiredSignatureError, InvalidTokenError, PyJWKClient

from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.hazmat.primitives import serialization

from blocks_genesis._auth.blocks_context import BlocksContext, BlocksContextManager
from blocks_genesis._cache import CacheClient
from blocks_genesis._cache.cache_provider import CacheProvider
from blocks_genesis._database.db_context import DbContext
from blocks_genesis._lmt.activity import Activity
from blocks_genesis._tenant.tenant import Tenant
from blocks_genesis._tenant.tenant_service import TenantService, get_tenant_service



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

    tenant_id = BlocksContextManager.get_context().tenant_id if BlocksContextManager.get_context() else None
    tenant = await tenant_service.get_tenant(tenant_id)
    is_third_party_token = False

    header = request.headers.get("Authorization")
    if header and any(header.startswith(prefix) for prefix in ["bearer ", "Bearer "]):
        token = header.split(" ", 1)[1].strip()
    else:
        bc = BlocksContextManager.get_context()
        token = request.cookies.get(f"access_token_{bc.tenant_id}", "")
        if not token and (ck := getattr(tenant.third_party_jwt_token_parameters, "cookie_key", None)):
            token = request.cookies.get(ck, "")
            is_third_party_token = True
        
    if not token:
        raise HTTPException(401, "Token missing")
    
    if is_third_party_token:
     return await try_fallback_async(request=request, token=token, tenant = tenant , db_context=DbContext.get_provider())

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

        blocks_context = BlocksContextManager.create_from_jwt_claims(extended_payload)
        BlocksContextManager.set_context(blocks_context)
        Activity.set_current_property("baggage.UserId", blocks_context.user_id)
        Activity.set_current_property("baggage.IsAuthenticate", "true")
        
        return extended_payload
    except ExpiredSignatureError as e:
      print(f"JWT expired: {e}")
      raise HTTPException(401, "Token expired")
    
    except InvalidTokenError as e:
     print(f"JWT verification failed: {e}, trying fallback...")

     validation_result = await try_fallback_async(request=request, token=token,tenant=tenant, db_context=DbContext.get_provider())

     if not validation_result:
        raise HTTPException(401, f"Invalid token: {e}")

     return validation_result

    

def create_certificate(certificate_data: bytes, password: str = None):
    try:
        password_bytes = password.encode('utf-8') if password else None
        certificate = pkcs12.load_pkcs12(certificate_data, password_bytes)
        return certificate.additional_certs[0].certificate
    except Exception as e:
        print(f"Failed to create certificate: {e}")
        return None
    

async def try_fallback_async(
    request,
    token: str,
    tenant: Tenant,
    db_context: DbContext,
    ex: Optional[Exception] = None
) -> bool:
  
    if ex is not None:
        print(f"[Fallback] Triggered due to: {type(ex).__name__} - {ex}")

    try:
        if not token or token.strip() == "":
            print("[Fallback] ❌ No token found in request.")
            return False

        tp = getattr(tenant, "third_party_jwt_token_parameters", None)
        if tp is None:
            print("[Fallback] ❌ Tenant has no ThirdPartyJwtTokenParameters.")
            return False

        # Prefer JWKS if present
        if tp.jwks_url:
            payload = await _get_from_jwks_url(token, tp)
        else:
            payload = await _get_from_public_certificate(token, tp)

        if payload is None:
            print("[Fallback] ❌ Fallback validation failed.")
            return False

        validated = await _validate_token_with_fallback_async(payload, token, request, db_context)
        return validated

    except Exception as final_ex:
        print(f"[Fallback] 💥 Unhandled error: {final_ex}")
        return False


# --------------------------
# JWKS-based retrieval + validation
# --------------------------
async def _get_from_jwks_url(token: str, tp_params) -> Optional[Dict[str, Any]]:

    jwks_url = tp_params.jwks_url
    if not jwks_url:
        return None

    try:
        # PyJWKClient is synchronous; run in threadpool to avoid blocking event loop
        loop = asyncio.get_running_loop()

        def _get_signing_key():
            client = PyJWKClient(jwks_url)
            signing_key = client.get_signing_key_from_jwt(token)
            return signing_key.key

        public_key = await loop.run_in_executor(None, _get_signing_key)

        # decode
        options = {"verify_signature": True, "verify_exp": True, "verify_nbf": True, "verify_iat": True}
        payload = jwt.decode(
            token,
            key=public_key,
            algorithms=["RS256", "RS384", "RS512"],
            issuer=tp_params.issuer or None,
            audience=tp_params.audiences or None,
            options=options,
            leeway=0
        )
        print("[Fallback] ✅ Token validated via JWKS.")
        return payload

    except Exception as e:
        print(f"[Fallback] ❌ JWKS validation failed: {e}")
        return None


# --------------------------
# Public certificate-based retrieval + validation
# --------------------------
async def _get_from_public_certificate(token: str, tp_params) -> Optional[Dict[str, Any]]:
   
    try:
        cert_path = tp_params.public_certificate_path
        if not cert_path:
            print("[Fallback] ❌ No public certificate path configured.")
            return None

        cert_bytes = await fetch_cert_bytes(cert_path)
        if cert_bytes is None:
            print("[Fallback] ❌ Failed to load public certificate bytes.")
            return None

        cert = create_certificate(cert_bytes, tp_params.public_certificate_password or "")
        if cert is None:
            print("[Fallback] ❌ Failed to create certificate object.")
            return None

        public_key = cert.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        )

        options = {"verify_signature": True, "verify_exp": True, "verify_nbf": True, "verify_iat": True}
        payload = jwt.decode(
            token,
            key=public_key,
            algorithms=["RS256", "RS384", "RS512"],
            issuer=tp_params.issuer or None,
            audience=tp_params.audiences or None,
            options=options,
            leeway=0
        )

        print("[Fallback] ✅ Token validated via public certificate.")
        return payload

    except Exception as e:
        print(f"[Fallback] ❌ Public certificate validation failed: {e}")
        return None


# --------------------------
# Final validation step + store BlocksContext
# --------------------------
async def _validate_token_with_fallback_async(
    payload: Dict[str, Any],
    token: str,
    request,
    db_context: DbContext
) -> bool:
    
    try:
        # Attach request uri and token to payload (like AddClaims in C#)
        payload = dict(payload)  # make a copy
        payload[BlocksContext.REQUEST_URI_CLAIM] = str(request.url)
        payload[BlocksContext.TOKEN_CLAIM] = token

        # Populate BlocksContext from payload (assumes you have this method)
        blocks_context = BlocksContextManager.create_from_jwt_claims(payload)
        BlocksContextManager.set_context(blocks_context)

        # Activity baggage
        Activity.set_current_property("baggage.UserId", blocks_context.user_id or "")
        Activity.set_current_property("baggage.IsAuthenticate", "true")

        # Map and store third-party context fields (reads mapping from DB)
        await _store_third_party_blocks_context_activity(payload, request, db_context)

        print("[Fallback] ✅ Fallback flow finished successfully.")
        return True

    except Exception as e:
        print(f"[Fallback] ❌ Validation+context store failed: {e}")
        return False


# --------------------------
# Claim mapping & BlocksContext creation (C# StoreThirdPartyBlocksContextActivity equivalent)
# --------------------------
async def _store_third_party_blocks_context_activity(
    payload: Dict[str, Any],
    request,
    db_context: DbContext
):
    try:
        # find mapping doc (the C# code takes the first doc)
        coll = await db_context.get_collection("ThirdPartyJWTClaims")
        claims_mapper = await coll.find_one({}) or {}

        # helper functions to parse nested claim object like "user.roles" or "roles"
        def _extract_claim_property(claim_object: str) -> str:
            parts = claim_object.split(".")
            return parts[-1] if parts else claim_object

        def _get_claim_object_name(claim_object: str) -> str:
            parts = claim_object.split(".")
            return parts[0] if parts else claim_object

        def _extract_claim_value_from_payload(payload_obj: Dict[str, Any], claim_object: str) -> str:
            # If claim_object is nested like "profile.name" - traverse
            parts = claim_object.split(".")
            cur = payload_obj
            try:
                for p in parts:
                    if isinstance(cur, dict):
                        cur = cur.get(p)
                    else:
                        return ""
                # if the final is not a primitive, return its JSON string representation
                if isinstance(cur, (dict, list)):
                    return json.dumps(cur)
                return str(cur) if cur is not None else ""
            except Exception:
                return ""

        # roles resolution
        roles: List[str] = []
        role_claims_key = claims_mapper.get("Roles", "")
        if role_claims_key:
            # It might be a nested object or an array in payload
            # e.g. claim mapping Roles -> "permissions.roles" or "roles"
            obj_name = _get_claim_object_name(role_claims_key)
            # find the claim in top-level payload
            claim_value = payload.get(obj_name)
            if claim_value is None:
                # not a top-level claim; attempt to extract via full path
                val = _extract_claim_value_from_payload(payload, role_claims_key)
                try:
                    arr = json.loads(val) if isinstance(val, str) and val.startswith(("[", "{")) else None
                except Exception:
                    arr = None

                if isinstance(arr, list):
                    roles = [str(x) for x in arr if x is not None]
                elif val:
                    # if val is comma separated string or single value
                    if isinstance(val, str) and val.startswith("["):
                        # already tried JSON parse, fallback to empty
                        roles = []
                    elif isinstance(val, str) and "," in val:
                        roles = [r.strip() for r in val.split(",") if r.strip()]
                    elif val:
                        roles = [val]
            else:
                # top-level claim exists
                if isinstance(claim_value, list):
                    roles = [str(x) for x in claim_value if x is not None]
                elif isinstance(claim_value, str):
                    try:
                        parsed = json.loads(claim_value)
                        if isinstance(parsed, list):
                            roles = [str(x) for x in parsed if x is not None]
                        else:
                            roles = [claim_value]
                    except Exception:
                        roles = [claim_value]

        # fallback: if still empty, look for standard role claim type
        if not roles:
            # common role claim keys
            for k in ("roles", "role", "http://schemas.microsoft.com/ws/2008/06/identity/claims/role"):
                val = payload.get(k)
                if isinstance(val, list):
                    roles = [str(x) for x in val if x is not None]
                    break
                elif isinstance(val, str):
                    roles = [val]
                    break

        # user identifier
        sub_claim = payload.get("sub", "") or payload.get("nameid", "") or payload.get("user_id", "")
        email_claim = payload.get("email", "")

        # Build userId according to mapping (C# logic)
        user_id_mapper = claims_mapper.get("UserId", "")
        if user_id_mapper:
            # if mapping says "sub" then keep sub, otherwise extract nested value and append "_external"
            if _extract_claim_property(user_id_mapper) == "sub":
                user_id = sub_claim
            else:
                extracted = _extract_claim_value_from_payload(payload, user_id_mapper)
                user_id = (extracted + "_external") if extracted else (sub_claim + "_external" if sub_claim else "")
        else:
            user_id = sub_claim or ""

        # expireOn: use 'exp' from token (numeric) -> convert to datetime
        exp_val = payload.get("exp")
        expire_on = datetime.fromtimestamp(int(exp_val), tz=timezone.utc) if exp_val else None

        # username and display name mapping
        def _map_field(mapper_key: str, fallback_field: str = "") -> str:
            mapper_val = claims_mapper.get(mapper_key, "")
            if mapper_val.lower() == "email":
                return email_claim or ""
            if mapper_val:
                return _extract_claim_value_from_payload(payload, mapper_val)
            return fallback_field or ""

        user_name = _map_field("UserName", "")
        display_name = _map_field("Name", "")

        # email mapping: if not set, try mapper
        email = email_claim or _extract_claim_value_from_payload(payload, claims_mapper.get("Email", "") or "")

        # prepare BlocksContext payload (match your BlocksContext.create signature)
        # the C# code sets tenantId to apiKey (blocks header), but we will set actualTenantId to apiKey and tenantId to it as well
        api_key = request.headers.get("blocks-key", "")  # C# used header BlocksConstants.BlocksKey

        # Create BlocksContext (you might have a factory method; adapt as needed)
        blocks_ctx = BlocksContext.create(
            tenant_id=api_key,
            roles=roles,
            user_id=user_id,
            is_authenticated=True,
            request_uri=str(request.url),
            organization_id="",
            expire_on=expire_on,
            email=email or "",
            permissions=[],
            user_name=user_name or "",
            phone_number="",
            display_name=display_name or "",
            oauth_token=payload.get("oauth", None),
            actual_tent_id=api_key
        )

        BlocksContextManager.set_context(blocks_ctx)

        # Store serialized context on request.state so downstream handlers can access it.
        request.state.third_party_context_json = json.dumps(blocks_ctx.dict() if hasattr(blocks_ctx, "dict") else {})

        # also optionally set a header-like bag in request.state for downstream response middleware to copy into a header
        request.state.third_party_context_header = request.state.third_party_context_json

        print("[Fallback] ✅ Third-party context stored from claims mapper.")
    except Exception as e:
        print(f"[Fallback] ❌ Error storing third-party blocks context: {e}")



async def extract_project_key(request: Request) -> str | None:
    # 1. Query param
    if "ProjectKey" in request.query_params:
        v = request.query_params.get("ProjectKey")
        if v:
            return v

    # 2. JSON body (must allow buffering)
    try:
        body_bytes = await request.body()
        if body_bytes:
            body_json = json.loads(body_bytes.decode("utf-8"))
            project_key = body_json.get("projectKey")
            if project_key:
                return project_key
    except Exception:
        pass

    return None


async def is_project_owner_or_shared(user_id: str, project_key: str, db_context: DbContext, tenants: TenantService):
    # Check if this tenant is created by the user (owner check)
    tenant = await tenants.get_tenant(project_key)
    if tenant and tenant.created_by == user_id:
        return True

    # Check shared project (ProjectPeoples collection)
    collection = await db_context.get_collection("ProjectPeoples", tenant_id=project_key)
    query = {"UserId": user_id, "TenantId": project_key}
    shared = await collection.find_one(query)
    return shared is not None


async def handle_root_tenant_access(
    request: Request,
    context: BlocksContext,
    tenants: TenantService,
    db_context: DbContext
) -> bool:

    blocks_key = request.headers.get("blocks-key")
    if not blocks_key:
        return False

    tenant = await tenants.get_tenant(blocks_key)
    if tenant is None or not tenant.is_root_tenant:
        return False

    project_key = await extract_project_key(request)
    if not project_key:
        return False

    user_id = context.user_id
    if not user_id:
        return False

    allowed = await is_project_owner_or_shared(user_id, project_key, db_context, tenants)
    return allowed


async def check_standard_access(
    context: BlocksContext,
    controller: str,
    action: str,
    db_context: DbContext
) -> bool:

    roles = context.roles or []
    permissions = context.permissions or []

    resource = f"{context.service_name}::{controller}::{action}".lower()

    collection = await db_context.get_collection("Permissions", tenant_id=context.tenant_id)

    query = {
        "Type": 1,
        "Resource": resource,
        "$or": [
            {"Roles": {"$in": roles}},
            {"Name": {"$in": permissions}}
        ]
    }

    count = await collection.count_documents(query)
    return count > 0


def authorize(bypass_authorization: bool = False):
    async def dependency(request: Request):
        tenant_service = TenantService()
        cache_client = CacheProvider.get_client()
        db_context = DbContext.get_provider()

        # 1. Authenticate (your JWT logic)
        await authenticate(request, tenant_service, cache_client)

        context = BlocksContextManager.get_context()
        if not context:
            raise HTTPException(401, "Missing context")

        # 2. Bypass if requested
        if bypass_authorization:
            return

        # 3. Extract controller & action (same as C# ControllerActionDescriptor)
        path_parts = request.url.path.strip("/").split("/")
        if len(path_parts) >= 4:
            controller = path_parts[2]
            action = path_parts[3]
        elif len(path_parts) >= 2:
            controller = path_parts[0]
            action = path_parts[1]
        else:
            raise HTTPException(400, "Invalid URL format.")

        # ---------------------------------------------------------
        #         ROOT TENANT SPECIAL ACCESS (C# equivalent)
        # ---------------------------------------------------------
        is_root_allowed = await handle_root_tenant_access(
            request=request,
            context=context,
            tenants=tenant_service,
            db_context=db_context
        )

        if is_root_allowed:
            return  # Allowed immediately like context.Succeed()

        # ---------------------------------------------------------
        #              STANDARD PERMISSION CHECK
        # ---------------------------------------------------------
        allowed = await check_standard_access(
            context=context,
            controller=controller,
            action=action,
            db_context=db_context
        )

        if not allowed:
            raise HTTPException(403, "Insufficient permissions")

    return Depends(dependency)
