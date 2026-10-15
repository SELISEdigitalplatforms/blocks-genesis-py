"""What a token from a tenant's own identity provider must carry, and what it may not.

Story: ABC Ltd (ABC001) trusts Auth0. Sara signs in through Auth0, so Auth0 signs her
token. Her Auth0 admin can write any claim into it, including the ones Genesis writes
into its own tokens.
"""
import datetime
import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest
from starlette.datastructures import URL

from blocks_genesis._auth import auth
from blocks_genesis._auth.blocks_context import BlocksContext, BlocksContextManager
from blocks_genesis._auth.third_party_provider import ThirdPartyJwtProvider
from blocks_genesis._delegation import constants as delegation_constants
from blocks_genesis._delegation.context import AuthClaimsContext, DelegatedTokenContext
from blocks_genesis._delegation.grant_factory import DelegationGrantFactory
from blocks_genesis._tenant.tenant import Tenant
from blocks_genesis._utilities.crypto_service import CryptoService

AUTH0 = "https://abc.eu.auth0.com/"
AUDIENCE = "api://klax"
SALT = "8f14e45fceea167a5a36dedd4bea2543"
SECRET = "the-shared-secret-of-at-least-32-bytes"
SECURITY_LOGGER = "blocks_genesis._auth.security_log"

CONFIGURATION_EVENTS = (
    "third_party_provider_issuer_not_checked",
    "third_party_provider_audience_not_checked",
    "third_party_provider_default_organization",
)


@pytest.fixture(autouse=True)
def clean_contexts():
    BlocksContextManager.clear_context()
    AuthClaimsContext.clear()
    DelegatedTokenContext.clear()
    yield
    BlocksContextManager.clear_context()
    AuthClaimsContext.clear()
    DelegatedTokenContext.clear()


def _tenant():
    return Tenant(_id="ABC001", TenantId="ABC001", TenantSalt=SALT, IsThirdPartyJwtEnabled=True)


def _provider(**kwargs):
    defaults = dict(
        _id="p1", TenantId="ABC001", Key="auth0-main", IsActive=True,
        Issuer=AUTH0, Audiences=[AUDIENCE], Algorithms=[10],
        SigningSecretCipher=CryptoService.encrypt(SECRET, SALT),
        ClaimsMapping={"UserId": "sub"},
    )
    defaults.update(kwargs)
    return ThirdPartyJwtProvider(**defaults)


def _request(headers=None):
    request = MagicMock()
    request.url = URL("https://api.klax.io/x")
    request.headers = headers or {}
    request.cookies = {}
    request.query_params = {}
    return request


def _sign(drop=(), **overrides):
    now = datetime.datetime.now(datetime.timezone.utc)
    claims = {
        "sub": "auth0|sara_123",
        "iss": AUTH0,
        "aud": AUDIENCE,
        "iat": now,
        "nbf": now,
        "exp": now + datetime.timedelta(minutes=30),
    }
    claims.update(overrides)
    for name in drop:
        del claims[name]
    return jwt.encode(claims, SECRET, algorithm="HS256")


async def _validate(token, provider=None):
    store = MagicMock()
    store.get_active = AsyncMock(return_value=[provider or _provider()])
    with patch.object(auth, "get_third_party_provider_store", return_value=store):
        return await auth.validate_with_fallback(token, _tenant(), _request(), None)


def _events(caplog):
    """Every [Security] line as its parsed payload, with the record's level added."""
    found = []
    for record in caplog.records:
        message = record.getMessage()
        if message.startswith("[Security]"):
            payload = json.loads(message[len("[Security] "):])
            payload["level"] = record.levelno
            found.append(payload)
    return found


def _named(caplog, event_name):
    return [e for e in _events(caplog) if e["eventName"] == event_name]


# ---------------------------------------------------------------------------
# Lifetime
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_token_without_exp_is_refused(caplog):
    with caplog.at_level(logging.INFO, logger=SECURITY_LOGGER):
        assert await _validate(_sign(drop=("exp",))) is None

    failures = _named(caplog, "fallback_validation_failed")
    assert len(failures) == 1
    assert failures[0]["exceptionType"] == "MissingRequiredClaimError"


@pytest.mark.asyncio
async def test_a_token_without_iat_is_still_accepted():
    """blocks-genesis-net requires exp only, so a provider that omits iat works in both."""
    result = await _validate(_sign(drop=("iat",)))

    assert result is not None
    assert result.claims["sub"] == "auth0|sara_123"


@pytest.mark.asyncio
async def test_a_token_with_exp_and_iat_is_accepted():
    result = await _validate(_sign())

    assert result is not None
    assert result.claims["sub"] == "auth0|sara_123"


@pytest.mark.asyncio
async def test_the_third_party_leeway_stays_at_five_minutes():
    now = datetime.datetime.now(datetime.timezone.utc)
    issued = now - datetime.timedelta(minutes=30)

    assert auth.THIRD_PARTY_LEEWAY_SECONDS == 300
    assert await _validate(
        _sign(iat=issued, nbf=issued, exp=now - datetime.timedelta(minutes=4))
    ) is not None
    assert await _validate(
        _sign(iat=issued, nbf=issued, exp=now - datetime.timedelta(minutes=6))
    ) is None


@pytest.mark.asyncio
async def test_a_token_issued_beyond_the_leeway_in_the_future_is_refused():
    now = datetime.datetime.now(datetime.timezone.utc)

    assert await _validate(_sign(iat=now + datetime.timedelta(minutes=10))) is None


# ---------------------------------------------------------------------------
# Reserved claims
# ---------------------------------------------------------------------------

def test_every_blocks_claim_but_the_jwt_standard_ones_is_reserved():
    standard = {
        BlocksContext.ISSUER_CLAIM,
        BlocksContext.AUDIENCES_CLAIM,
        BlocksContext.ISSUED_AT_TIME_CLAIM,
        BlocksContext.NOT_BEFORE_THAT_CLAIM,
        BlocksContext.EXPIRE_ON_CLAIM,
        BlocksContext.SUBJECT_CLAIM,
    }
    blocks_claims = {
        getattr(BlocksContext, name)
        for name in dir(BlocksContext)
        if name.endswith("_CLAIM")
    }
    reserved = set(BlocksContextManager.RESERVED_CLAIMS)

    assert blocks_claims - standard <= reserved
    assert "token_version" in reserved
    assert "security_stamp" in reserved


def test_delegation_claim_names_match_the_delegation_module():
    assert BlocksContext.TOKEN_VERSION_CLAIM == delegation_constants.TOKEN_VERSION_CLAIM
    assert BlocksContext.SECURITY_STAMP_CLAIM == delegation_constants.SECURITY_STAMP_CLAIM


def test_strip_removes_delegation_session_and_request_claims():
    claims = {
        "sub": "auth0|sara_123",
        "token_version": 7,
        "security_stamp": "forged",
        "oauth": "forged-token",
        "request_uri": "/forged",
        "sid": "forged-sid",
        "phone": "+10000000000",
        "nickname": "sara",
    }

    assert BlocksContextManager.strip_reserved_claims(claims) == {
        "sub": "auth0|sara_123",
        "nickname": "sara",
    }


FORGED = {
    "sub": "auth0|sara_123",
    "iss": AUTH0,
    "aud": AUDIENCE,
    "token_version": 7,
    "security_stamp": "forged",
    "oauth": "forged-token",
    "sid": "forged-sid",
    "request_uri": "/forged",
    "phone": "+10000000000",
}


async def _authenticate_forged():
    tenant_service = MagicMock()
    tenant_service.get_tenant = AsyncMock(return_value=_tenant())
    store = MagicMock()
    store.get_active = AsyncMock(return_value=[_provider()])

    with patch.object(auth, "extract_token_from_request", new_callable=AsyncMock) as extract, \
         patch.object(auth, "get_third_party_provider_store", return_value=store), \
         patch.object(auth, "_read_token_routing", return_value=(AUTH0, [AUDIENCE])), \
         patch.object(auth, "_resolve_provider_key", new_callable=AsyncMock) as key, \
         patch.object(auth.jwt, "decode", return_value=dict(FORGED)):
        extract.return_value = ("tok", True, "abc.klax.io")
        key.return_value = "a-key"

        BlocksContextManager.set_context(
            BlocksContextManager.create(tenant_id="ABC001", original_tenant_id="ABC001")
        )
        return await auth.authenticate(_request(), tenant_service, MagicMock())


@pytest.mark.asyncio
async def test_a_third_party_token_cannot_supply_grant_material():
    payload = await _authenticate_forged()

    assert AuthClaimsContext.version_material() == (None, None)
    for claims in (AuthClaimsContext.current(), payload):
        for name in ("token_version", "security_stamp", "oauth", "sid", "phone"):
            assert name not in claims, f"{name} reached a consumer"
        assert claims["request_uri"] == "/x"

    ctx = BlocksContextManager.get_context()
    assert ctx.session_id == ""
    assert ctx.oauth_token == ""
    assert ctx.phone_number == ""


@pytest.mark.asyncio
async def test_a_third_party_caller_gets_no_delegation_grant():
    await _authenticate_forged()
    store = MagicMock(create_async=AsyncMock(), get_async=AsyncMock())

    assert await DelegationGrantFactory(store).create_for_send_async() is None
    store.create_async.assert_not_awaited()
    store.get_async.assert_not_awaited()


@pytest.mark.asyncio
async def test_blocks_tokens_keep_their_delegation_and_session_claims():
    ahmed = {
        "tenant_id": "ABC001",
        "user_id": "ahmed_1",
        "roles": ["admin"],
        "org_id": "hq",
        "token_version": 3,
        "security_stamp": "stamp-3",
        "sid": "sid-1",
        "oauth": "tok",
    }
    tenant_service = MagicMock()
    tenant_service.get_tenant = AsyncMock(return_value=_tenant())

    with patch.object(auth, "extract_token_from_request", new_callable=AsyncMock) as extract, \
         patch.object(auth, "validate_jwt_token", new_callable=AsyncMock) as primary:
        extract.return_value = ("tok", False, "abc.klax.io")
        primary.return_value = dict(ahmed)

        BlocksContextManager.set_context(
            BlocksContextManager.create(tenant_id="ABC001", original_tenant_id="ABC001")
        )
        await auth.authenticate(_request(), tenant_service, MagicMock())

    ctx = BlocksContextManager.get_context()
    assert AuthClaimsContext.version_material() == ("3", "stamp-3")
    assert ctx.session_id == "sid-1"
    assert ctx.oauth_token == "tok"


# ---------------------------------------------------------------------------
# Provider settings that widen what is accepted: warned about, not refused
# ---------------------------------------------------------------------------

async def _validate_routed(providers, routing, calls=2):
    """Run the fallback `calls` times; `routing` is one (issuer, audiences) or one per call."""
    store = MagicMock()
    store.get_active = AsyncMock(return_value=list(providers))
    routes = routing if isinstance(routing, list) else [routing] * calls

    results = []
    with patch.object(auth, "get_third_party_provider_store", return_value=store), \
         patch.object(auth, "_read_token_routing", side_effect=routes), \
         patch.object(auth, "_resolve_provider_key", new_callable=AsyncMock, return_value="a-key"), \
         patch.object(auth.jwt, "decode", return_value={"sub": "auth0|sara_123"}) as decode:
        for _ in routes:
            results.append(await auth.validate_with_fallback("tok", _tenant(), _request(), None))
    return results, decode


@pytest.mark.asyncio
async def test_a_provider_without_an_issuer_is_used_and_warned_about_once(caplog):
    with caplog.at_level(logging.INFO, logger=SECURITY_LOGGER):
        results, decode = await _validate_routed([_provider(Issuer="")], ("", [AUDIENCE]))

    assert all(r is not None for r in results)
    warnings = _named(caplog, "third_party_provider_issuer_not_checked")
    assert len(warnings) == 1
    assert warnings[0]["level"] == logging.WARNING
    assert warnings[0]["detail"] == {"tenantId": "ABC001", "key": "auth0-main"}
    assert decode.call_args.kwargs["options"]["verify_iss"] is False


@pytest.mark.asyncio
async def test_a_provider_without_audiences_is_used_and_warned_about_once(caplog):
    issuer = "https://issuer-sentinel-7f3a.example/"
    with caplog.at_level(logging.INFO, logger=SECURITY_LOGGER):
        results, _ = await _validate_routed(
            [_provider(Issuer=issuer, Audiences=[])], (issuer, [AUDIENCE])
        )

    assert all(r is not None for r in results)
    warnings = _named(caplog, "third_party_provider_audience_not_checked")
    assert len(warnings) == 1
    assert warnings[0]["level"] == logging.WARNING
    for event in _events(caplog):
        if event["eventName"] in CONFIGURATION_EVENTS:
            assert issuer not in json.dumps(event)


@pytest.mark.asyncio
async def test_a_provider_on_the_default_organization_is_warned_about_once(caplog):
    with caplog.at_level(logging.INFO, logger=SECURITY_LOGGER):
        results, _ = await _validate_routed([_provider()], (AUTH0, [AUDIENCE]))

    assert all(r is not None for r in results)
    warnings = _named(caplog, "third_party_provider_default_organization")
    assert len(warnings) == 1
    assert warnings[0]["level"] == logging.WARNING
    assert warnings[0]["detail"] == {"tenantId": "ABC001", "key": "auth0-main"}


@pytest.mark.asyncio
async def test_each_provider_is_warned_about_separately(caplog):
    a = _provider(_id="pa", Key="a", Issuer="https://a.example/")
    b = _provider(_id="pb", Key="b", Issuer="https://b.example/")

    with caplog.at_level(logging.INFO, logger=SECURITY_LOGGER):
        await _validate_routed(
            [a, b], [("https://a.example/", [AUDIENCE]), ("https://b.example/", [AUDIENCE])]
        )

    warnings = _named(caplog, "third_party_provider_default_organization")
    assert [w["detail"]["key"] for w in warnings] == ["a", "b"]


@pytest.mark.asyncio
async def test_a_fully_configured_provider_logs_no_configuration_warning(caplog):
    with caplog.at_level(logging.INFO, logger=SECURITY_LOGGER):
        results, _ = await _validate_routed(
            [_provider(DefaultOrganizationId="hq")], (AUTH0, [AUDIENCE])
        )

    assert all(r is not None for r in results)
    assert not [e for e in _events(caplog) if e["eventName"] in CONFIGURATION_EVENTS]


@pytest.mark.asyncio
async def test_the_warning_memory_is_bounded():
    providers = [
        _provider(_id=f"p{i}", Key=f"k{i}", Issuer=f"https://{i}.example/") for i in range(3)
    ]

    with patch.object(auth, "PROVIDER_WARNINGS_MAX_ENTRIES", 2):
        await _validate_routed(
            providers, [(f"https://{i}.example/", [AUDIENCE]) for i in range(3)]
        )

    assert len(auth._provider_warnings_logged) <= 2
