"""Trust boundary for tokens minted outside Blocks.

Ports genesis-net `4041fbf` and `f9eb915`: a tenant must opt in before an external
token is looked at, and nothing the external provider wrote may reach BlocksContext.

Story used throughout: ABC Ltd is tenant ABC001. Sara signs in through Auth0, so Auth0
signs her token, not Blocks. XYZ Ltd is tenant XYZ001 -- a different customer she must
never reach.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from blocks_genesis._auth import auth
from blocks_genesis._auth.blocks_context import BlocksContext, BlocksContextManager
from blocks_genesis._auth.third_party_provider import ThirdPartyJwtProvider
from blocks_genesis._tenant.tenant import Tenant, ThirdPartyJwtTokenParameters

AUTH0 = "https://abc.eu.auth0.com/"


def _provider(**kwargs):
    defaults = dict(
        _id="p1",
        TenantId="ABC001",
        Key="auth0-main",
        IsActive=True,
        Issuer=AUTH0,
        Audiences=["api://klax"],
        Algorithms=[1],
        JwksUrl="https://abc.eu.auth0.com/.well-known/jwks.json",
        ClaimsMapping={"UserId": "sub"},
    )
    defaults.update(kwargs)
    return ThirdPartyJwtProvider(**defaults)


def _store(providers):
    store = MagicMock()
    store.get_active = AsyncMock(return_value=list(providers))
    return store


def _tenant(enabled: bool) -> Tenant:
    return Tenant(
        _id="ABC001",
        TenantId="ABC001",
        IsThirdPartyJwtEnabled=enabled,
        ThirdPartyJwtTokenParameters=ThirdPartyJwtTokenParameters(
            Issuer="https://abc.eu.auth0.com/",
            JwksUrl="https://abc.eu.auth0.com/.well-known/jwks.json",
            Audiences=["api://klax"],
        ),
    )


# ---------------------------------------------------------------------------
# Change 1 -- the on/off switch
# ---------------------------------------------------------------------------

def test_switch_defaults_to_off():
    assert Tenant(_id="t", TenantId="ABC001").is_third_party_jwt_enabled is False


def test_switch_reads_the_bson_name():
    assert Tenant(**{"_id": "t", "TenantId": "ABC001", "IsThirdPartyJwtEnabled": True}).is_third_party_jwt_enabled


def _request(headers=None):
    request = MagicMock()
    request.url = "https://api.klax.io/x"
    request.headers = headers or {}
    request.cookies = {}
    request.query_params = {}
    return request


@pytest.mark.asyncio
async def test_switch_off_refuses_even_with_a_provider_configured():
    """ABC Ltd tried Auth0 once and left the settings behind. The switch is off,
    so Sara is refused and the provider store is never even consulted."""
    store = _store([_provider()])

    with patch.object(auth, "get_third_party_provider_store", return_value=store):
        result = await auth.validate_with_fallback("tok", _tenant(enabled=False), _request())

    assert result is None
    store.get_active.assert_not_awaited()


@pytest.mark.asyncio
async def test_switch_on_consults_the_provider_store():
    store = _store([_provider()])

    with patch.object(auth, "get_third_party_provider_store", return_value=store),          patch.object(auth, "_read_token_routing", return_value=(AUTH0, ["api://klax"])),          patch.object(auth, "_resolve_provider_key", new_callable=AsyncMock) as key,          patch.object(auth.jwt, "decode", return_value={"sub": "auth0|sara_123"}):
        key.return_value = "a-key"
        result = await auth.validate_with_fallback("tok", _tenant(enabled=True), _request())

    assert result is not None
    assert result.provider.key == "auth0-main"
    store.get_active.assert_awaited_once_with("ABC001")


@pytest.mark.asyncio
async def test_an_uninitialised_store_refuses_the_token():
    with patch.object(auth, "get_third_party_provider_store", return_value=None):
        result = await auth.validate_with_fallback("tok", _tenant(enabled=True), _request())

    assert result is None


@pytest.mark.asyncio
async def test_a_request_declaring_another_tenant_is_refused():
    """The tenant is never taken from the token, and never from a header that
    disagrees with the tenant already loaded."""
    store = _store([_provider()])

    with patch.object(auth, "get_third_party_provider_store", return_value=store):
        result = await auth.validate_with_fallback(
            "tok", _tenant(enabled=True), _request({"x-blocks-key": "XYZ001"})
        )

    assert result is None
    store.get_active.assert_not_awaited()


# ---------------------------------------------------------------------------
# Change 7 -- the wash
# ---------------------------------------------------------------------------

SARA_TOKEN = {
    "sub": "auth0|sara_123",
    "iss": "https://abc.eu.auth0.com/",
    "aud": "api://klax",
    # everything below is written by Sara's Auth0 admin, not by us
    "tenant_id": "XYZ001",
    "original_tenant_id": "XYZ001",
    "user_id": "admin",
    "user_name": "admin",
    "name": "Administrator",
    "email": "admin@xyz.example",
    "org_id": "finance",
    "permissions": ["*"],
    "roles": ["superadmin"],
    "impersonated": True,
    "impersonation_session_id": "forged",
    "client_id": "forged",
}


def test_strip_reserved_claims_removes_every_reserved_name():
    kept = BlocksContextManager.strip_reserved_claims(SARA_TOKEN)

    for reserved in BlocksContextManager.RESERVED_CLAIMS:
        assert reserved not in kept, f"{reserved} survived the wash"


def test_strip_reserved_claims_keeps_everything_else():
    kept = BlocksContextManager.strip_reserved_claims(SARA_TOKEN)

    assert kept["sub"] == "auth0|sara_123"
    assert kept["iss"] == "https://abc.eu.auth0.com/"
    assert kept["aud"] == "api://klax"


def test_strip_reserved_claims_does_not_mutate_the_original():
    BlocksContextManager.strip_reserved_claims(SARA_TOKEN)

    assert SARA_TOKEN["tenant_id"] == "XYZ001"


def test_context_tenant_comes_from_the_tenant_record_not_the_token():
    ctx = BlocksContextManager.create_third_party_context(
        tenant_id="ABC001", subject="auth0|sara_123"
    )

    assert ctx.tenant_id == "ABC001"
    assert ctx.original_tenant_id == "ABC001"


def test_context_permissions_are_always_empty():
    ctx = BlocksContextManager.create_third_party_context(
        tenant_id="ABC001", subject="auth0|sara_123"
    )

    assert ctx.permissions == []


def test_context_roles_come_only_from_the_mapping():
    unmapped = BlocksContextManager.create_third_party_context(
        tenant_id="ABC001", subject="auth0|sara_123"
    )
    mapped = BlocksContextManager.create_third_party_context(
        tenant_id="ABC001", subject="auth0|sara_123", roles=["reader"]
    )

    assert unmapped.roles == []
    assert mapped.roles == ["reader"]


def test_context_organization_comes_from_the_provider():
    default = BlocksContextManager.create_third_party_context(
        tenant_id="ABC001", subject="auth0|sara_123"
    )
    narrowed = BlocksContextManager.create_third_party_context(
        tenant_id="ABC001", subject="auth0|sara_123", organization_id="support"
    )

    assert default.organization_id == "default"
    assert narrowed.organization_id == "support"


def test_context_is_never_impersonated():
    ctx = BlocksContextManager.create_third_party_context(
        tenant_id="ABC001", subject="auth0|sara_123"
    )

    assert ctx.impersonated is False
    assert ctx.impersonation_session_id == ""
    assert ctx.client_id == ""


def test_context_user_id_carries_the_external_suffix():
    ctx = BlocksContextManager.create_third_party_context(
        tenant_id="ABC001", subject="auth0|sara_123"
    )

    assert ctx.user_id == "auth0|sara_123_external"


def test_context_refuses_a_subject_that_did_not_resolve():
    """.NET lets a bare "_external" through, which every broken mapping collapses onto.
    Python fails closed instead -- see part 14.5 of the port document."""
    with pytest.raises(ValueError):
        BlocksContextManager.create_third_party_context(tenant_id="ABC001", subject="")


def test_context_refuses_a_blank_tenant():
    with pytest.raises(ValueError):
        BlocksContextManager.create_third_party_context(
            tenant_id="", subject="auth0|sara_123"
        )


# ---------------------------------------------------------------------------
# The two together: Sara's crafted token cannot reach XYZ Ltd
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_saras_crafted_claims_never_reach_the_context():
    request = MagicMock()
    request.url = "https://api.klax.io/x"
    request.headers = {}
    request.cookies = {}
    request.query_params = {}

    tenant_service = MagicMock()
    tenant_service.get_tenant = AsyncMock(return_value=_tenant(enabled=True))

    with patch.object(auth, "extract_token_from_request", new_callable=AsyncMock) as extract, \
         patch.object(auth, "get_third_party_provider_store", return_value=_store([_provider()])), \
         patch.object(auth, "_read_token_routing", return_value=(AUTH0, ["api://klax"])), \
         patch.object(auth, "_resolve_provider_key", new_callable=AsyncMock) as key, \
         patch.object(auth.jwt, "decode", return_value=dict(SARA_TOKEN)):
        extract.return_value = ("tok", True, "abc.klax.io")
        key.return_value = "a-key"

        BlocksContextManager.set_context(
            BlocksContextManager.create(tenant_id="ABC001", original_tenant_id="ABC001")
        )
        await auth.authenticate(request, tenant_service, MagicMock())

    ctx = BlocksContextManager.get_context()
    assert ctx.tenant_id == "ABC001"
    assert ctx.original_tenant_id == "ABC001"
    assert ctx.permissions == []
    assert ctx.roles == []
    assert ctx.organization_id == "default"
    assert ctx.impersonated is False
    assert ctx.user_id == "auth0|sara_123_external"


@pytest.mark.asyncio
async def test_blocks_tokens_are_untouched_by_the_wash():
    """Ahmed's token is ours. Its claims are read exactly as before."""
    request = MagicMock()
    request.url = "https://api.klax.io/x"
    request.headers = {}
    request.cookies = {}
    request.query_params = {}

    ahmed = {
        "tenant_id": "ABC001",
        "user_id": "ahmed_1",
        "roles": ["admin"],
        "permissions": ["agents:read"],
        "org_id": "hq",
        "email": "ahmed@abc.example",
    }

    tenant_service = MagicMock()
    tenant_service.get_tenant = AsyncMock(return_value=_tenant(enabled=True))

    with patch.object(auth, "extract_token_from_request", new_callable=AsyncMock) as extract, \
         patch.object(auth, "validate_jwt_token", new_callable=AsyncMock) as primary:
        extract.return_value = ("tok", False, "abc.klax.io")
        primary.return_value = dict(ahmed)

        BlocksContextManager.set_context(
            BlocksContextManager.create(tenant_id="ABC001", original_tenant_id="ABC001")
        )
        await auth.authenticate(request, tenant_service, MagicMock())

    ctx = BlocksContextManager.get_context()
    assert ctx.tenant_id == "ABC001"
    assert ctx.user_id == "ahmed_1"
    assert ctx.roles == ["admin"]
    assert ctx.permissions == ["agents:read"]
    assert ctx.organization_id == "hq"


# ---------------------------------------------------------------------------
# Change 8 -- which validator gets the token, and in what order
# ---------------------------------------------------------------------------

async def _authenticate(request, tenant, *, primary=None, fallback=None):
    tenant_service = MagicMock()
    tenant_service.get_tenant = AsyncMock(return_value=tenant)

    with patch.object(auth, "extract_token_from_request", new_callable=AsyncMock) as extract, \
         patch.object(auth, "validate_jwt_token", new_callable=AsyncMock) as prim, \
         patch.object(auth, "validate_with_fallback", new_callable=AsyncMock) as fall, \
         patch.object(auth, "_resolve_signing_tenant", new_callable=AsyncMock) as signer:
        extract.return_value = ("tok", False, "abc.klax.io")
        signer.return_value = tenant
        if isinstance(primary, BaseException):
            prim.side_effect = primary
        else:
            prim.return_value = primary
        fall.return_value = fallback

        BlocksContextManager.set_context(
            BlocksContextManager.create(tenant_id="ABC001", original_tenant_id="ABC001")
        )
        try:
            await auth.authenticate(request, tenant_service, MagicMock())
            raised = None
        except Exception as err:  # noqa: BLE001 - the test asserts on it
            raised = err

    return prim, fall, raised


@pytest.mark.asyncio
async def test_a_bearer_external_token_reaches_the_provider_path():
    """A bearer token is not flagged third-party by the cookie check, so before this it
    only reached the fallback by accident -- and PyJWT's InvalidTokenError escaped the
    HTTPException catch, so it never got there at all."""
    from jwt import InvalidSignatureError

    accepted = auth.ThirdPartyValidation({"sub": "auth0|sara_123"}, _provider())
    prim, fall, raised = await _authenticate(
        _request(), _tenant(enabled=True),
        primary=InvalidSignatureError("bad signature"), fallback=accepted,
    )

    assert raised is None
    fall.assert_awaited()
    assert BlocksContextManager.get_context().user_id == "auth0|sara_123_external"


@pytest.mark.asyncio
async def test_the_fallback_runs_before_primary_when_the_switch_is_on():
    """genesis-net routes an opted-in tenant on the token's own issuer, so an accepted
    provider token never costs a wasted primary attempt."""
    accepted = auth.ThirdPartyValidation({"sub": "auth0|sara_123"}, _provider())
    prim, fall, raised = await _authenticate(
        _request(), _tenant(enabled=True), fallback=accepted
    )

    assert raised is None
    fall.assert_awaited()
    prim.assert_not_awaited()


@pytest.mark.asyncio
async def test_primary_still_gets_its_turn_when_the_fallback_refuses():
    """Not ours, or ours and broken. Either way primary gets its turn, so a provider
    token caught mid key-rotation is not locked out by one failure."""
    prim, fall, raised = await _authenticate(
        _request(), _tenant(enabled=True),
        primary={"tenant_id": "ABC001", "user_id": "ahmed_1"}, fallback=None,
    )

    assert raised is None
    fall.assert_awaited()
    prim.assert_awaited()
    assert BlocksContextManager.get_context().user_id == "ahmed_1"


@pytest.mark.asyncio
async def test_a_tenant_with_the_switch_off_never_calls_the_fallback():
    prim, fall, raised = await _authenticate(
        _request(), _tenant(enabled=False),
        primary={"tenant_id": "ABC001", "user_id": "ahmed_1"},
    )

    assert raised is None
    fall.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_invalid_blocks_token_is_a_401_not_a_500():
    from fastapi import HTTPException
    from jwt import InvalidSignatureError

    prim, fall, raised = await _authenticate(
        _request(), _tenant(enabled=False), primary=InvalidSignatureError("bad"),
    )

    assert isinstance(raised, HTTPException)
    assert raised.status_code == 401


@pytest.mark.asyncio
async def test_the_trace_flag_reports_the_real_outcome():
    """A bearer external token is not flagged third-party at extraction time, so the
    flag has to come from what actually validated it."""
    accepted = auth.ThirdPartyValidation({"sub": "auth0|sara_123"}, _provider())

    with patch.object(auth, "Activity") as activity:
        await _authenticate(_request(), _tenant(enabled=True), fallback=accepted)

    activity.set_current_property.assert_any_call("baggage.IsThirdPartyToken", "True")


# ---------------------------------------------------------------------------
# Change 8 -- the quiet rule on the terminal log line
# ---------------------------------------------------------------------------

def _events(caplog):
    import json as _json
    found = []
    for record in caplog.records:
        message = record.getMessage()
        if message.startswith("[Security]"):
            found.append(_json.loads(message[len("[Security] "):])["eventName"])
    return found


@pytest.mark.asyncio
async def test_a_blocks_token_does_not_log_a_rejection(caplog):
    """Every ordinary Blocks token looks like ISSUER_UNMATCHED. Logging that as a
    rejection would flood the channel and hide the events that matter."""
    import logging

    store = _store([_provider()])
    with caplog.at_level(logging.INFO, logger="blocks_genesis._auth.security_log"), \
         patch.object(auth, "get_third_party_provider_store", return_value=store), \
         patch.object(auth, "_read_token_routing", return_value=("SeliseBlocks", [])):
        assert await auth.validate_with_fallback("tok", _tenant(enabled=True), _request()) is None

    assert "third_party_provider_unmatched" in _events(caplog)
    assert "fallback_rejected" not in _events(caplog)


@pytest.mark.asyncio
async def test_a_tenant_with_no_rows_does_not_log_a_rejection(caplog):
    import logging

    with caplog.at_level(logging.INFO, logger="blocks_genesis._auth.security_log"), \
         patch.object(auth, "get_third_party_provider_store", return_value=_store([])):
        assert await auth.validate_with_fallback("tok", _tenant(enabled=True), _request()) is None

    assert "fallback_rejected" not in _events(caplog)


@pytest.mark.asyncio
async def test_a_real_failure_logs_the_terminal_line(caplog):
    """An ambiguous selection is a genuine fault, so it gets the line that says the
    request is about to be answered 401."""
    import logging

    rows = [_provider(Key="auth0-app1"), _provider(Key="auth0-app2", _id="p2")]
    with caplog.at_level(logging.INFO, logger="blocks_genesis._auth.security_log"), \
         patch.object(auth, "get_third_party_provider_store", return_value=_store(rows)), \
         patch.object(auth, "_read_token_routing", return_value=(AUTH0, ["api://klax"])):
        assert await auth.validate_with_fallback("tok", _tenant(enabled=True), _request()) is None

    assert "third_party_provider_ambiguous" in _events(caplog)
    assert "fallback_rejected" in _events(caplog)


@pytest.mark.asyncio
async def test_an_unexpected_error_in_the_fallback_refuses_rather_than_crashing():
    """Primary validation still gets its turn. An escaping exception would 500 the
    request instead."""
    store = MagicMock()
    store.get_active = AsyncMock(side_effect=RuntimeError("something unforeseen"))

    with patch.object(auth, "get_third_party_provider_store", return_value=store):
        assert await auth.validate_with_fallback("tok", _tenant(enabled=True), _request()) is None


@pytest.mark.asyncio
async def test_no_tenant_at_all_is_refused():
    assert await auth.validate_with_fallback("tok", None, _request()) is None
