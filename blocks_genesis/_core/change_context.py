import json
from fastapi import Request
from blocks_genesis._auth.blocks_context import BlocksContext, BlocksContextManager
from blocks_genesis._database.db_context import DbContext
from blocks_genesis._lmt.activity import Activity
from blocks_genesis._tenant.tenant_service import get_tenant_service
from motor.motor_asyncio import AsyncIOMotorCollection


async def change_context(request: Request, project_key: str):
    # STEP 1: Read third-party context header (same as C#)
    await apply_third_party_context(request)

    # After third-party context applied, read the current context
    context = BlocksContextManager.get_context()

    # Track actual tenant in OTel baggage
    Activity.set_current_property("baggage.ActualTenantId", context.tenant_id)

    # Skip if project_key invalid or same as current tenant
    if not project_key or project_key == context.tenant_id:
        return

    tenant_service = get_tenant_service()
    tenant = await tenant_service.get_tenant(project_key)

    # Check whether the user is in the shared project
    collection: AsyncIOMotorCollection = await  DbContext.get_provider().get_collection("ProjectPeoples")
    shared_project =  collection.find_one(
        {"UserId": context.user_id, "TenantId": project_key}
    )

    # Check root tenant flag
    is_root = (await tenant_service.get_tenant(context.tenant_id)).is_root_tenant

    # Same C# condition:
    # isRoot && (tenant.CreatedBy == userId || sharedProject exists)
    if is_root and (tenant.created_by == context.user_id or shared_project):
        BlocksContextManager.set_context(
            BlocksContextManager.create(
                tenant_id=project_key,
                roles=context.roles,
                user_id=context.user_id,
                is_authenticated=context.is_authenticated,
                request_uri=context.request_uri,
                organization_id=context.organization_id,
                expire_on=context.expire_on,
                email=context.email,
                permissions=context.permissions,
                user_name=context.user_name,
                phone_number=context.phone_number,
                display_name=context.display_name,
                oauth_token=context.oauth_token,
                actual_tenant_id=context.tenant_id,
            )
        )


async def apply_third_party_context(request: Request):
    
    third_party_header = getattr(request.state, "third_party_context_header", None)

    if not third_party_header:
        return

    try:
        context_dict = json.loads(third_party_header)
    except json.JSONDecodeError:
        return

    # Create new context object
    new_context = BlocksContext.create(
        tenant_id=context_dict.get("tenant_id", ""),
        roles=context_dict.get("roles", []),
        user_id=context_dict.get("user_id", ""),
        is_authenticated=context_dict.get("is_authenticated", False),
        request_uri=context_dict.get("request_uri", ""),
        organization_id=context_dict.get("organization_id", ""),
        expire_on=context_dict.get("expire_on"),
        email=context_dict.get("email", ""),
        permissions=context_dict.get("permissions", []),
        user_name=context_dict.get("user_name", ""),
        phone_number=context_dict.get("phone_number", ""),
        display_name=context_dict.get("display_name", ""),
        oauth_token=context_dict.get("oauth_token", ""),
        actual_tenant_id=context_dict.get("tenant_id", "")
    )

    BlocksContextManager.set_context(new_context)
