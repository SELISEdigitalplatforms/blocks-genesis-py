"""The token exchange stays on the identity service, and its token cache is keyed by tenant."""

import json

import pytest

from blocks_genesis._auth.blocks_context import BlocksContextManager
from blocks_genesis._delegation import constants, endpoint_resolver
from blocks_genesis._delegation.constants import IAM_BASE_URL_KEY, IAM_TOKEN_ENDPOINT_KEY
from blocks_genesis._delegation.context import DelegatedTokenContext
from blocks_genesis._delegation.endpoint_resolver import DelegationTokenEndpointResolver
from blocks_genesis._delegation.token_provider import DelegatedTokenProvider

TENANT_ID = "tenant-1"
OTHER_TENANT_ID = "tenant-2"
GRANT_ID = "dg_" + "b" * 64
FALLBACK = "https://blocks-iam/api/oidc/token"


class FakeResponse:
    def __init__(self, status, payload=None, location=None):
        self.status = status
        self._payload = payload
        self.headers = {"Location": location} if location else {}

    async def json(self, content_type=None):
        return self._payload

    async def text(self):
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    """Behaves like aiohttp: a 3xx is followed unless the caller passes allow_redirects=False."""

    def __init__(self, recorder, respond):
        self._recorder = recorder
        self._respond = respond

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def _request(self, url, kwargs):
        self._recorder["urls"].append(url)
        self._recorder["kwargs"].append(kwargs)
        response = self._respond(url)
        if 300 <= response.status < 400 and kwargs.get("allow_redirects", True):
            return self._respond(response.headers["Location"])
        return response

    def get(self, url, **kwargs):
        return self._request(url, kwargs)

    def post(self, url, **kwargs):
        return self._request(url, kwargs)


def install_discovery(monkeypatch, respond):
    recorder = {"urls": [], "kwargs": []}
    monkeypatch.setattr(
        endpoint_resolver.aiohttp, "ClientSession", lambda *a, **k: FakeSession(recorder, respond)
    )
    return recorder


def discovery_of(token_endpoint):
    return lambda url: FakeResponse(200, {"token_endpoint": token_endpoint})


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch):
    monkeypatch.delenv(IAM_BASE_URL_KEY, raising=False)
    monkeypatch.delenv(IAM_TOKEN_ENDPOINT_KEY, raising=False)
    monkeypatch.setattr(endpoint_resolver, "get_configurations", lambda: {})
    BlocksContextManager.set_context(BlocksContextManager.create(tenant_id=TENANT_ID, user_id="user-1"))
    DelegatedTokenContext.clear()
    yield
    BlocksContextManager.clear_context()
    DelegatedTokenContext.clear()


# --- Pinned discovery -------------------------------------------------------------------------

FOREIGN_ENDPOINTS = [
    pytest.param("https://evil.example/api/oidc/token", id="other-host"),
    pytest.param("https://blocks-iam:8443/api/oidc/token", id="other-port"),
    pytest.param("http://blocks-iam/api/oidc/token", id="http-downgrade"),
    pytest.param("https://blocks-iam@evil.example/api/oidc/token", id="userinfo"),
    pytest.param("https://blocks-iam.evil.example/api/oidc/token", id="host-prefix"),
]


@pytest.mark.parametrize("token_endpoint", FOREIGN_ENDPOINTS)
async def test_a_foreign_token_endpoint_falls_back_to_the_configured_one(monkeypatch, token_endpoint):
    monkeypatch.setenv(IAM_BASE_URL_KEY, "https://blocks-iam")
    monkeypatch.setenv(IAM_TOKEN_ENDPOINT_KEY, FALLBACK)
    install_discovery(monkeypatch, discovery_of(token_endpoint))

    assert await DelegationTokenEndpointResolver().get_token_endpoint_async(TENANT_ID) == FALLBACK


@pytest.mark.parametrize("token_endpoint", FOREIGN_ENDPOINTS)
async def test_a_foreign_token_endpoint_without_a_fallback_raises(monkeypatch, token_endpoint):
    monkeypatch.setenv(IAM_BASE_URL_KEY, "https://blocks-iam")
    install_discovery(monkeypatch, discovery_of(token_endpoint))

    with pytest.raises(RuntimeError) as excinfo:
        await DelegationTokenEndpointResolver().get_token_endpoint_async(TENANT_ID)

    assert "Refusing to guess" in str(excinfo.value)


@pytest.mark.parametrize(
    "base_url, token_endpoint",
    [
        ("https://blocks-iam", "https://blocks-iam/api/oidc/token"),
        ("https://Blocks-IAM/", "https://blocks-iam:443/api/oidc/token"),
        ("http://blocks-iam:80", "HTTP://blocks-iam./api/oidc/token"),
        ("http://blocks-iam:8080/base", "http://blocks-iam:8080/api/oidc/token"),
    ],
)
async def test_a_same_origin_token_endpoint_is_accepted(monkeypatch, base_url, token_endpoint):
    monkeypatch.setenv(IAM_BASE_URL_KEY, base_url)
    install_discovery(monkeypatch, discovery_of(token_endpoint))

    assert await DelegationTokenEndpointResolver().get_token_endpoint_async(TENANT_ID) == token_endpoint


async def test_the_tenant_id_is_encoded_in_the_discovery_url(monkeypatch):
    monkeypatch.setenv(IAM_BASE_URL_KEY, "https://blocks-iam")
    recorder = install_discovery(monkeypatch, discovery_of("https://blocks-iam/api/oidc/token"))

    await DelegationTokenEndpointResolver().get_token_endpoint_async("a/b?c")

    assert recorder["urls"] == ["https://blocks-iam/a%2Fb%3Fc/.well-known/openid-configuration"]


# --- Discovery cache expiry -------------------------------------------------------------------


async def test_discovery_is_cached_until_it_expires(monkeypatch):
    monkeypatch.setenv(IAM_BASE_URL_KEY, "https://blocks-iam")
    recorder = install_discovery(monkeypatch, discovery_of("https://blocks-iam/api/oidc/token"))
    clock = {"now": 500.0}

    resolver = DelegationTokenEndpointResolver(time_func=lambda: clock["now"])

    await resolver.get_token_endpoint_async(TENANT_ID)
    clock["now"] += constants.DISCOVERY_CACHE_SECONDS - 1
    await resolver.get_token_endpoint_async(TENANT_ID)
    assert len(recorder["urls"]) == 1

    clock["now"] += 1
    await resolver.get_token_endpoint_async(TENANT_ID)
    assert len(recorder["urls"]) == 2


# --- No redirects -----------------------------------------------------------------------------


async def test_discovery_does_not_follow_a_redirect(monkeypatch):
    monkeypatch.setenv(IAM_BASE_URL_KEY, "https://blocks-iam")
    monkeypatch.setenv(IAM_TOKEN_ENDPOINT_KEY, FALLBACK)

    def respond(url):
        if url.endswith("/elsewhere"):
            return FakeResponse(200, {"token_endpoint": "https://blocks-iam/redirected/token"})
        return FakeResponse(302, location="https://blocks-iam/elsewhere")

    recorder = install_discovery(monkeypatch, respond)

    assert await DelegationTokenEndpointResolver().get_token_endpoint_async(TENANT_ID) == FALLBACK
    assert recorder["kwargs"][0].get("allow_redirects") is False


class FakeTenant:
    tenant_salt = "salt-value"


class FakeTenantService:
    async def get_tenant(self, tenant_id):
        return FakeTenant()


class FakeResolver:
    async def get_token_endpoint_async(self, tenant_id):
        return "https://blocks-iam/api/oidc/token"


def make_provider(respond):
    recorder = {"urls": [], "kwargs": []}
    provider = DelegatedTokenProvider(
        tenant_service=FakeTenantService(),
        endpoint_resolver=FakeResolver(),
        session_factory=lambda: FakeSession(recorder, respond),
        time_func=lambda: 1_000_000.0,
    )
    return provider, recorder


def token_response(access_token):
    return FakeResponse(200, json.dumps({"access_token": access_token, "expires_in": 300}))


async def test_the_exchange_does_not_follow_a_redirect():
    def respond(url):
        if url.endswith("/elsewhere"):
            return token_response("redirected-token")
        return FakeResponse(302, location="https://blocks-iam/elsewhere")

    provider, recorder = make_provider(respond)
    DelegatedTokenContext.set(GRANT_ID)

    assert await provider.get_token_async() is None
    assert recorder["kwargs"][0].get("allow_redirects") is False


# --- Cache keyed by tenant and grant ----------------------------------------------------------


def use_tenant(tenant_id):
    BlocksContextManager.set_context(BlocksContextManager.create(tenant_id=tenant_id, user_id="user-1"))


async def test_tenants_sharing_a_grant_id_do_not_share_a_token():
    calls = {"n": 0}

    def respond(url):
        calls["n"] += 1
        return token_response(f"access-{calls['n']}")

    provider, _ = make_provider(respond)
    DelegatedTokenContext.set(GRANT_ID)

    use_tenant(TENANT_ID)
    assert await provider.get_token_async() == "access-1"

    use_tenant(OTHER_TENANT_ID)
    assert await provider.get_token_async() == "access-2"

    use_tenant(TENANT_ID)
    assert await provider.get_token_async() == "access-1"
    assert calls["n"] == 2


async def test_invalidate_drops_the_grant_for_every_tenant():
    calls = {"n": 0}

    def respond(url):
        calls["n"] += 1
        return token_response(f"access-{calls['n']}")

    provider, _ = make_provider(respond)
    DelegatedTokenContext.set(GRANT_ID)

    for tenant_id in (TENANT_ID, OTHER_TENANT_ID):
        use_tenant(tenant_id)
        await provider.get_token_async()

    provider.invalidate(GRANT_ID)

    for tenant_id in (TENANT_ID, OTHER_TENANT_ID):
        use_tenant(tenant_id)
        await provider.get_token_async()

    assert calls["n"] == 4


async def test_sweep_drops_expired_entries_for_every_tenant():
    clock = {"now": 1_000_000.0}
    provider = DelegatedTokenProvider(
        tenant_service=FakeTenantService(),
        endpoint_resolver=FakeResolver(),
        session_factory=lambda: FakeSession({"urls": [], "kwargs": []}, lambda url: token_response("t")),
        time_func=lambda: clock["now"],
    )
    DelegatedTokenContext.set(GRANT_ID)

    for tenant_id in (TENANT_ID, OTHER_TENANT_ID):
        use_tenant(tenant_id)
        await provider.get_token_async()

    clock["now"] += 300
    provider._sweep_expired()

    assert provider._tokens == {}
    assert provider._locks == {}
