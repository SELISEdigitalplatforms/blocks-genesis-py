
from blocks_genesis._auth.blocks_context import BlocksContextManager
from blocks_genesis._database.db_context import DbContext
from blocks_genesis._lmt.activity import Activity
from blocks_genesis._tenant.tenant_service import get_tenant_service
from motor.motor_asyncio import AsyncIOMotorCollection


async def change_context(project_key: str):
    context = BlocksContextManager.get_context()

    # Track actual tenant in OTel baggage
    Activity.set_current_property("baggage.ActualTenantId", context.tenant_id)

    # Skip if project_key invalid or same as current tenant
    if not project_key or project_key == context.tenant_id:
        return

    tenant_service = get_tenant_service()
    tenant = await tenant_service.get_tenant(project_key)

    # Check whether the user is in the shared project
    collection: AsyncIOMotorCollection = await DbContext.get_provider().get_collection("ProjectPeoples")
    shared_project = collection.find_one(
        {"UserId": context.user_id, "TenantId": project_key}
    )

    # Check root tenant flag
    is_root = (await tenant_service.get_tenant(context.tenant_id)).is_root_tenant

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

        # Update baggage with the new tenant
        Activity.set_current_property("baggage.TenantId", project_key)
