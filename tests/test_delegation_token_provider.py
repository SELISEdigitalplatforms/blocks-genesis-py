"""Exchange, cache and single-flight behaviour of the delegated token provider."""

import asyncio
import json

import pytest

from blocks_genesis._auth.blocks_context import BlocksContextManager
from blocks_genesis._delegation import constants
from blocks_genesis._delegation.context import DelegatedTokenContext
from blocks_genesis._delegation.signature import sign
from blocks_genesis._delegation.token_provider import DelegatedTokenProvider

TENANT_ID = "tenant-1"
TENANT_SALT = "salt-value"
ENDPOINT = "http://blocks-iam:8080/api/oidc/token"
GRANT_ID = "dg_" + "a" * 64


class FakeTenant:
    def __init__(self, salt):
        self.tenant_salt = salt


class FakeTenantService:
    def __init__(self, salt=TENANT_SALT):
        self._salt = salt

    async def get_tenant(self, tenant_id):
        return FakeTenant(self._salt) if self._salt is not None else None


class FakeResolver:
    def __init__(self, endpoint=ENDPOINT):
        self.endpoint = endpoint
        self.calls = 0

    async def get_token_endpoint_async(self, tenant_id):
        self.calls += 1
        return self.endpoint


class FakeResponse:
    def __init__(self, status, body):
        self.status = status
        self._body = body

    async def text(self):
        return self._body

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    """Counts exchanges and records what was posted, so single-flight is observable."""

    def __init__(self, recorder, respond):
        self._recorder = recorder
        self._respond = respond

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def post(self, url, data=None, headers=None):
        self._recorder["calls"] += 1
        index = self._recorder["calls"]
        self._recorder["urls"].append(url)
        self._recorder["forms"].append(dict(data or {}))
        self._recorder["headers"].append(dict(headers or {}))
        return self._respond(index, self._recorder)


def token_body(access_token, expires_in=300):
    return json.dumps({"access_token": access_token, "token_type": "Bearer", "expires_in": expires_in})


def make_provider(respond, salt=TENANT_SALT, start_time=1_000_000.0):
    recorder = {"calls": 0, "urls": [], "forms": [], "headers": []}
    clock = {"now": start_time}

    provider = DelegatedTokenProvider(
        tenant_service=FakeTenantService(salt),
        endpoint_resolver=FakeResolver(),
        session_factory=lambda: FakeSession(recorder, respond),
        time_func=lambda: clock["now"],
    )

    return provider, recorder, clock


@pytest.fixture(autouse=True)
def ambient_context():
    BlocksContextManager.set_context(
        BlocksContextManager.create(
            tenant_id=TENANT_ID,
            user_id="user-1",
            is_authenticated=True,
            organization_id="org-1",
        )
    )
    DelegatedTokenContext.clear()
    yield
    BlocksContextManager.clear_context()
    DelegatedTokenContext.clear()


async def test_returns_none_without_a_grant_in_scope():
    provider, recorder, _ = make_provider(lambda i, r: FakeResponse(200, token_body("t")))

    assert await provider.get_token_async() is None
    assert recorder["calls"] == 0


async def test_returns_none_when_context_carries_no_tenant():
    provider, recorder, _ = make_provider(lambda i, r: FakeResponse(200, token_body("t")))
    BlocksContextManager.clear_context()
    DelegatedTokenContext.set(GRANT_ID)

    assert await provider.get_token_async() is None
    assert recorder["calls"] == 0


async def test_posts_the_rfc8693_form_with_a_valid_signature_and_tenant_header():
    provider, recorder, clock = make_provider(lambda i, r: FakeResponse(200, token_body("access-1")))
    DelegatedTokenContext.set(GRANT_ID)

    assert await provider.get_token_async() == "access-1"

    form = recorder["forms"][0]
    assert form["grant_type"] == constants.TOKEN_EXCHANGE_GRANT_TYPE
    assert form["subject_token"] == GRANT_ID
    assert form["subject_token_type"] == constants.DELEGATION_GRANT_TOKEN_TYPE
    assert form["ts"] == str(int(clock["now"]))
    assert len(form["nonce"]) == 32
    assert form["sig"] == sign(TENANT_ID, GRANT_ID, form["nonce"], int(clock["now"]), TENANT_SALT)

    assert recorder["headers"][0][constants.BLOCKS_KEY_HEADER] == TENANT_ID
    assert recorder["urls"][0] == ENDPOINT


async def test_serves_the_cached_token_inside_validity():
    provider, recorder, _ = make_provider(lambda i, r: FakeResponse(200, token_body(f"access-{i}")))
    DelegatedTokenContext.set(GRANT_ID)

    assert await provider.get_token_async() == "access-1"
    assert await provider.get_token_async() == "access-1"
    assert await provider.get_token_async() == "access-1"

    assert recorder["calls"] == 1


async def test_refetches_once_inside_the_renewal_margin():
    # A 300s lifetime stops being served at 240s.
    provider, recorder, clock = make_provider(
        lambda i, r: FakeResponse(200, token_body(f"access-{i}", expires_in=300))
    )
    DelegatedTokenContext.set(GRANT_ID)

    assert await provider.get_token_async() == "access-1"

    clock["now"] += 239
    assert await provider.get_token_async() == "access-1"
    assert recorder["calls"] == 1

    clock["now"] += 2
    assert await provider.get_token_async() == "access-2"
    assert recorder["calls"] == 2


async def test_fifty_concurrent_callers_perform_exactly_one_exchange():
    gate = asyncio.Event()

    class GatedResponse(FakeResponse):
        async def __aenter__(self):
            await gate.wait()
            return self

    provider, recorder, _ = make_provider(
        lambda i, r: GatedResponse(200, token_body(f"access-{i}"))
    )
    DelegatedTokenContext.set(GRANT_ID)

    callers = [asyncio.create_task(provider.get_token_async()) for _ in range(50)]
    await asyncio.sleep(0)
    gate.set()

    tokens = await asyncio.gather(*callers)

    assert recorder["calls"] == 1
    assert all(token == "access-1" for token in tokens)


async def test_a_rejection_costs_one_round_trip_and_is_not_cached():
    def respond(index, recorder):
        if index == 1:
            return FakeResponse(400, json.dumps({"error": "invalid_grant"}))
        return FakeResponse(200, token_body("access-recovered"))

    provider, recorder, _ = make_provider(respond)
    DelegatedTokenContext.set(GRANT_ID)

    assert await provider.get_token_async() is None
    assert recorder["calls"] == 1

    # Nothing negative was cached, so a later call is free to try again.
    assert await provider.get_token_async() == "access-recovered"
    assert recorder["calls"] == 2


async def test_returns_none_when_the_exchange_raises():
    def respond(index, recorder):
        raise ConnectionError("connection refused")

    provider, _, _ = make_provider(respond)
    DelegatedTokenContext.set(GRANT_ID)

    assert await provider.get_token_async() is None


async def test_returns_none_when_the_tenant_has_no_salt():
    provider, recorder, _ = make_provider(
        lambda i, r: FakeResponse(200, token_body("t")), salt=None
    )
    DelegatedTokenContext.set(GRANT_ID)

    assert await provider.get_token_async() is None
    assert recorder["calls"] == 0


async def test_returns_none_when_the_response_has_no_access_token():
    provider, _, _ = make_provider(lambda i, r: FakeResponse(200, json.dumps({"token_type": "Bearer"})))
    DelegatedTokenContext.set(GRANT_ID)

    assert await provider.get_token_async() is None


async def test_returns_none_when_the_response_is_not_json():
    provider, _, _ = make_provider(lambda i, r: FakeResponse(200, "<html>gateway error</html>"))
    DelegatedTokenContext.set(GRANT_ID)

    assert await provider.get_token_async() is None


async def test_a_response_without_expires_in_gets_a_short_conservative_lifetime():
    provider, recorder, clock = make_provider(
        lambda i, r: FakeResponse(200, json.dumps({"access_token": f"access-{i}"}))
    )
    DelegatedTokenContext.set(GRANT_ID)

    # Twice the renewal margin, so it is servable for the margin's length and then refetched.
    assert await provider.get_token_async() == "access-1"

    clock["now"] += constants.TOKEN_RENEWAL_MARGIN_SECONDS - 1
    assert await provider.get_token_async() == "access-1"
    assert recorder["calls"] == 1

    clock["now"] += 2
    assert await provider.get_token_async() == "access-2"
    assert recorder["calls"] == 2


async def test_invalidate_drops_the_cached_token():
    provider, recorder, _ = make_provider(lambda i, r: FakeResponse(200, token_body(f"access-{i}")))
    DelegatedTokenContext.set(GRANT_ID)

    assert await provider.get_token_async() == "access-1"

    provider.invalidate(GRANT_ID)

    assert await provider.get_token_async() == "access-2"
    assert recorder["calls"] == 2


async def test_delegated_auth_headers_attaches_bearer_and_tenant_header(monkeypatch):
    from blocks_genesis._delegation import token_provider

    provider, _, _ = make_provider(lambda i, r: FakeResponse(200, token_body("access-1")))
    monkeypatch.setattr(token_provider, "_provider", provider)
    DelegatedTokenContext.set(GRANT_ID)

    headers = await token_provider.delegated_auth_headers({"Accept": "application/json"})

    assert headers["Authorization"] == "Bearer access-1"
    assert headers[constants.BLOCKS_KEY_HEADER] == TENANT_ID
    assert headers["Accept"] == "application/json"


async def test_delegated_auth_headers_never_overrides_an_existing_authorization(monkeypatch):
    from blocks_genesis._delegation import token_provider

    provider, recorder, _ = make_provider(lambda i, r: FakeResponse(200, token_body("access-1")))
    monkeypatch.setattr(token_provider, "_provider", provider)
    DelegatedTokenContext.set(GRANT_ID)

    headers = await token_provider.delegated_auth_headers({"Authorization": "Bearer caller-supplied"})

    assert headers["Authorization"] == "Bearer caller-supplied"
    assert recorder["calls"] == 0


async def test_delegated_auth_headers_is_a_no_op_without_a_grant(monkeypatch):
    from blocks_genesis._delegation import token_provider

    provider, recorder, _ = make_provider(lambda i, r: FakeResponse(200, token_body("access-1")))
    monkeypatch.setattr(token_provider, "_provider", provider)
    DelegatedTokenContext.clear()

    assert await token_provider.delegated_auth_headers() == {}
    assert recorder["calls"] == 0
