"""The security context as it travels on a message.

Ports genesis-net `8b85991`: the `sid` claim rides along, and the payload matches
`BlocksContext.CreateSanitizedForTransport` so a .NET service and a Python worker can
read each other's messages.

Before this, .NET sent PascalCase and Python restored with snake_case keyword arguments,
so a .NET message reached a Python worker, raised TypeError, was swallowed, and the
worker ran with no tenant at all.
"""
import json

from blocks_genesis._auth.blocks_context import BlocksContextManager


def _ahmed():
    return BlocksContextManager.create(
        tenant_id="ABC001",
        original_tenant_id="ABC001",
        user_id="ahmed_1",
        roles=["admin"],
        permissions=["agents:read"],
        organization_id="hq",
        email="ahmed@abc.example",
        phone_number="+8801712345678",
        user_name="ahmed",
        display_name="Ahmed",
        oauth_token="a-real-token",
        application_domain="abc.klax.io",
        is_authenticated=True,
        session_id="sess_9",
        client_id="portal",
    )


# ---------------------------------------------------------------------------
# What goes onto the wire
# ---------------------------------------------------------------------------

def test_wire_payload_uses_the_dotnet_names():
    wire = BlocksContextManager.create_sanitized_for_transport(_ahmed())

    assert set(wire) == {
        "TenantId", "Roles", "UserId", "IsAuthenticated", "RequestUri",
        "OrganizationId", "ExpireOn", "Email", "Permissions", "UserName",
        "PhoneNumber", "DisplayName", "OauthToken", "OriginalTenantId",
        "ApplicationDomain", "Impersonated", "ClientId", "SessionId",
    }


def test_wire_payload_carries_the_session_id():
    assert BlocksContextManager.create_sanitized_for_transport(_ahmed())["SessionId"] == "sess_9"


def test_wire_payload_masks_the_email():
    assert BlocksContextManager.create_sanitized_for_transport(_ahmed())["Email"] == "***@abc.example"


def test_wire_payload_masks_an_email_with_no_at_sign():
    ctx = BlocksContextManager.create(tenant_id="ABC001", email="notanemail")

    assert BlocksContextManager.create_sanitized_for_transport(ctx)["Email"] == "***"


def test_wire_payload_masks_the_phone_number():
    assert BlocksContextManager.create_sanitized_for_transport(_ahmed())["PhoneNumber"] == "***5678"


def test_wire_payload_never_carries_the_token():
    assert BlocksContextManager.create_sanitized_for_transport(_ahmed())["OauthToken"] == ""


def test_wire_payload_leaves_out_the_impersonation_session_id():
    """.NET carries Impersonated but not ImpersonationSessionId."""
    wire = BlocksContextManager.create_sanitized_for_transport(_ahmed())

    assert "ImpersonationSessionId" not in wire


def test_wire_payload_falls_back_to_the_tenant_for_original_tenant():
    ctx = BlocksContextManager.create(tenant_id="ABC001")

    assert BlocksContextManager.create_sanitized_for_transport(ctx)["OriginalTenantId"] == "ABC001"


def test_wire_payload_of_nothing_is_empty():
    assert BlocksContextManager.create_sanitized_for_transport(None) == {}


def test_wire_payload_is_json_serialisable():
    json.dumps(BlocksContextManager.create_sanitized_for_transport(_ahmed()))


# ---------------------------------------------------------------------------
# What comes back off the wire
# ---------------------------------------------------------------------------

def test_a_dotnet_payload_restores():
    """The case that silently lost the whole context before."""
    dotnet = {
        "TenantId": "ABC001",
        "Roles": ["admin"],
        "UserId": "ahmed_1",
        "IsAuthenticated": True,
        "RequestUri": "api.klax.io",
        "OrganizationId": "hq",
        "ExpireOn": "2026-09-21T10:00:00",
        "Email": "***@abc.example",
        "Permissions": ["agents:read"],
        "UserName": "ahmed",
        "PhoneNumber": "***5678",
        "DisplayName": "Ahmed",
        "OauthToken": "",
        "OriginalTenantId": "ABC001",
        "ApplicationDomain": "abc.klax.io",
        "Impersonated": False,
        "ClientId": "portal",
        "SessionId": "sess_9",
    }

    ctx = BlocksContextManager.from_transport(dotnet)

    assert ctx.tenant_id == "ABC001"
    assert ctx.user_id == "ahmed_1"
    assert ctx.roles == ["admin"]
    assert ctx.permissions == ["agents:read"]
    assert ctx.organization_id == "hq"
    assert ctx.session_id == "sess_9"
    assert ctx.client_id == "portal"
    assert ctx.is_authenticated is True


def test_a_legacy_snake_case_payload_still_restores():
    """Messages already on the queue when this ships."""
    ctx = BlocksContextManager.from_transport(
        {"tenant_id": "ABC001", "user_id": "ahmed_1", "roles": ["admin"]}
    )

    assert ctx.tenant_id == "ABC001"
    assert ctx.user_id == "ahmed_1"
    assert ctx.roles == ["admin"]


def test_an_unknown_key_is_ignored():
    ctx = BlocksContextManager.from_transport({"TenantId": "ABC001", "SomethingNew": 1})

    assert ctx.tenant_id == "ABC001"


def test_an_empty_payload_gives_nothing():
    assert BlocksContextManager.from_transport({}) is None
    assert BlocksContextManager.from_transport(None) is None


def test_a_round_trip_keeps_every_field_that_travels():
    wire = BlocksContextManager.create_sanitized_for_transport(_ahmed())
    back = BlocksContextManager.from_transport(json.loads(json.dumps(wire)))

    assert back.tenant_id == "ABC001"
    assert back.original_tenant_id == "ABC001"
    assert back.user_id == "ahmed_1"
    assert back.roles == ["admin"]
    assert back.permissions == ["agents:read"]
    assert back.organization_id == "hq"
    assert back.application_domain == "abc.klax.io"
    assert back.session_id == "sess_9"
    assert back.client_id == "portal"
    assert back.oauth_token == ""
