from blocks_genesis._auth.blocks_context import BlocksContextManager
from blocks_genesis._lmt.activity import Activity
from blocks_genesis._tenant.tenant_service import get_tenant_service


async def change_context(project_key: str):
    context = BlocksContextManager.get_context()
    
    Activity.set_current_property("baggage.ActualTenantId", context.tenant_id)
    
    if project_key is None or project_key == "" or project_key == context.tenant_id:
        return
    
    is_root = (await get_tenant_service().get_tenant(context.tenant_id)).is_root_tenant
    
    if is_root:
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
                actual_tenant_id=context.tenant_id
            )
        )
        
        Activity.set_current_property("baggage.TenantId", project_key)
        
        