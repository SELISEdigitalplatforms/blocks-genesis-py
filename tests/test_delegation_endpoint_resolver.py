"""Section 5.5: discovery first, a complete configured URL as fallback, never a guessed path."""

import json

import pytest

from blocks_genesis._delegation import endpoint_resolver
from blocks_genesis._delegation.constants import (
    FRONTEND_RUNTIME_SECTION,
    IAM_BASE_URL_KEY,
    IAM_TOKEN_ENDPOINT_KEY,
)
from blocks_genesis._delegation.endpoint_resolver import DelegationTokenEndpointResolver

TENANT_ID = "tenant-1"


class FakeResponse:
    def __init__(self, status, payload):
        self.status = status
        self._payload = payload

    async def json(self, content_type=None):
        if isinstance(self._payload, str):
            return json.loads(self._payload)
        return self._payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    def __init__(self, recorder, respond):
        self._recorder = recorder
        self._respond = respond

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def get(self, url):
        self._recorder["urls"].append(url)
        return self._respond(len(self._recorder["urls"]))


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch):
    """Every test starts with both keys unset and no configuration file loaded."""
    monkeypatch.delenv(IAM_BASE_URL_KEY, raising=False)
    monkeypatch.delenv(IAM_TOKEN_ENDPOINT_KEY, raising=False)
    monkeypatch.setattr(endpoint_resolver, "get_configurations", lambda: {})
    yield


def install_session(monkeypatch, respond):
    recorder = {"urls": []}

    class SessionFactory:
        def __init__(self, *args, **kwargs):
            self._session = FakeSession(recorder, respond)

        async def __aenter__(self):
            return await self._session.__aenter__()

        async def __aexit__(self, *exc):
            return await self._session.__aexit__(*exc)

    monkeypatch.setattr(endpoint_resolver.aiohttp, "ClientSession", SessionFactory)
    return recorder


def discovery(token_endpoint):
    return lambda attempt: FakeResponse(200, {"token_endpoint": token_endpoint})


def test_ensure_configured_raises_when_neither_key_is_set():
    resolver = DelegationTokenEndpointResolver()

    with pytest.raises(RuntimeError) as excinfo:
        resolver.ensure_configured()

    assert IAM_BASE_URL_KEY in str(excinfo.value)
    assert IAM_TOKEN_ENDPOINT_KEY in str(excinfo.value)


def test_ensure_configured_passes_with_only_the_base_url(monkeypatch):
    monkeypatch.setenv(IAM_BASE_URL_KEY, "http://blocks-iam:8080")
    DelegationTokenEndpointResolver().ensure_configured()


def test_ensure_configured_passes_with_only_the_fallback_endpoint(monkeypatch):
    monkeypatch.setenv(IAM_TOKEN_ENDPOINT_KEY, "http://blocks-iam:8080/api/oidc/token")
    DelegationTokenEndpointResolver().ensure_configured()


async def test_discovery_queries_the_per_tenant_document(monkeypatch):
    monkeypatch.setenv(IAM_BASE_URL_KEY, "http://blocks-iam:8080/")
    recorder = install_session(monkeypatch, discovery("http://blocks-iam:8080/api/oidc/token?tenant_id=tenant-1"))

    resolver = DelegationTokenEndpointResolver()
    endpoint = await resolver.get_token_endpoint_async(TENANT_ID)

    assert endpoint == "http://blocks-iam:8080/api/oidc/token?tenant_id=tenant-1"
    assert recorder["urls"] == [f"http://blocks-iam:8080/{TENANT_ID}/.well-known/openid-configuration"]


async def test_a_successful_discovery_is_cached_per_tenant(monkeypatch):
    monkeypatch.setenv(IAM_BASE_URL_KEY, "http://blocks-iam:8080")
    recorder = install_session(monkeypatch, discovery("http://blocks-iam:8080/api/oidc/token"))

    resolver = DelegationTokenEndpointResolver()
    for _ in range(3):
        await resolver.get_token_endpoint_async(TENANT_ID)

    assert len(recorder["urls"]) == 1


async def test_falls_back_to_the_configured_url_when_discovery_is_unreachable(monkeypatch):
    monkeypatch.setenv(IAM_BASE_URL_KEY, "http://blocks-iam:8080")
    monkeypatch.setattr(
        endpoint_resolver,
        "get_configurations",
        lambda: {IAM_TOKEN_ENDPOINT_KEY: "http://blocks-iam:8080/api/oidc/token"},
    )

    def refuse(attempt):
        raise ConnectionError("connection refused")

    install_session(monkeypatch, refuse)

    resolver = DelegationTokenEndpointResolver()

    assert await resolver.get_token_endpoint_async(TENANT_ID) == "http://blocks-iam:8080/api/oidc/token"


async def test_discovery_is_retried_lazily_after_using_the_fallback(monkeypatch):
    monkeypatch.setenv(IAM_BASE_URL_KEY, "http://blocks-iam:8080")
    monkeypatch.setattr(
        endpoint_resolver,
        "get_configurations",
        lambda: {IAM_TOKEN_ENDPOINT_KEY: "http://fallback:8080/api/oidc/token"},
    )

    def respond(attempt):
        if attempt == 1:
            raise ConnectionError("boot: IAM not up yet")
        return FakeResponse(200, {"token_endpoint": "http://blocks-iam:8080/api/oidc/token"})

    recorder = install_session(monkeypatch, respond)
    resolver = DelegationTokenEndpointResolver()

    assert await resolver.get_token_endpoint_async(TENANT_ID) == "http://fallback:8080/api/oidc/token"
    assert await resolver.get_token_endpoint_async(TENANT_ID) == "http://blocks-iam:8080/api/oidc/token"
    assert len(recorder["urls"]) == 2


async def test_falls_back_when_discovery_returns_an_error_status(monkeypatch):
    monkeypatch.setenv(IAM_BASE_URL_KEY, "http://blocks-iam:8080")
    monkeypatch.setattr(
        endpoint_resolver,
        "get_configurations",
        lambda: {IAM_TOKEN_ENDPOINT_KEY: "http://blocks-iam:8080/api/oidc/token"},
    )
    install_session(monkeypatch, lambda attempt: FakeResponse(404, {}))

    resolver = DelegationTokenEndpointResolver()

    assert await resolver.get_token_endpoint_async(TENANT_ID) == "http://blocks-iam:8080/api/oidc/token"


async def test_falls_back_when_discovery_has_no_token_endpoint(monkeypatch):
    monkeypatch.setenv(IAM_BASE_URL_KEY, "http://blocks-iam:8080")
    monkeypatch.setattr(
        endpoint_resolver,
        "get_configurations",
        lambda: {IAM_TOKEN_ENDPOINT_KEY: "http://blocks-iam:8080/api/oidc/token"},
    )
    install_session(monkeypatch, lambda attempt: FakeResponse(200, {"issuer": "http://blocks-iam:8080"}))

    resolver = DelegationTokenEndpointResolver()

    assert await resolver.get_token_endpoint_async(TENANT_ID) == "http://blocks-iam:8080/api/oidc/token"


async def test_falls_back_when_discovery_returns_a_relative_token_endpoint(monkeypatch):
    monkeypatch.setenv(IAM_BASE_URL_KEY, "http://blocks-iam:8080")
    monkeypatch.setattr(
        endpoint_resolver,
        "get_configurations",
        lambda: {IAM_TOKEN_ENDPOINT_KEY: "http://blocks-iam:8080/api/oidc/token"},
    )
    install_session(monkeypatch, lambda attempt: FakeResponse(200, {"token_endpoint": "/api/oidc/token"}))

    resolver = DelegationTokenEndpointResolver()

    assert await resolver.get_token_endpoint_async(TENANT_ID) == "http://blocks-iam:8080/api/oidc/token"


async def test_raises_when_discovery_fails_and_there_is_no_fallback(monkeypatch):
    monkeypatch.setenv(IAM_BASE_URL_KEY, "http://blocks-iam:8080")

    def refuse(attempt):
        raise ConnectionError("down")

    install_session(monkeypatch, refuse)
    resolver = DelegationTokenEndpointResolver()

    with pytest.raises(RuntimeError) as excinfo:
        await resolver.get_token_endpoint_async(TENANT_ID)

    assert "Refusing to guess" in str(excinfo.value)


async def test_raises_without_a_tenant():
    resolver = DelegationTokenEndpointResolver()

    with pytest.raises(RuntimeError):
        await resolver.get_token_endpoint_async("")


async def test_reads_the_frontend_runtime_section(monkeypatch):
    # FrontendRuntime is where Blocks services put runtime settings, so this is the expected home.
    monkeypatch.setattr(
        endpoint_resolver,
        "get_configurations",
        lambda: {FRONTEND_RUNTIME_SECTION: {IAM_BASE_URL_KEY: "http://blocks-iam:8080"}},
    )
    recorder = install_session(monkeypatch, discovery("http://blocks-iam:8080/api/oidc/token"))

    resolver = DelegationTokenEndpointResolver()

    assert await resolver.get_token_endpoint_async(TENANT_ID) == "http://blocks-iam:8080/api/oidc/token"
    assert len(recorder["urls"]) == 1


async def test_still_reads_a_bare_top_level_key(monkeypatch):
    # A config file that sets the key at the top level, outside any section, still resolves.
    monkeypatch.setattr(
        endpoint_resolver, "get_configurations", lambda: {IAM_BASE_URL_KEY: "http://from-root:8080"}
    )
    recorder = install_session(monkeypatch, discovery("http://from-root:8080/api/oidc/token"))

    resolver = DelegationTokenEndpointResolver()

    assert await resolver.get_token_endpoint_async(TENANT_ID) == "http://from-root:8080/api/oidc/token"
    assert recorder["urls"][0].startswith("http://from-root:8080/")


async def test_prefers_the_frontend_runtime_section_over_the_top_level_key(monkeypatch):
    monkeypatch.setattr(
        endpoint_resolver,
        "get_configurations",
        lambda: {
            IAM_BASE_URL_KEY: "http://from-root:8080",
            FRONTEND_RUNTIME_SECTION: {IAM_BASE_URL_KEY: "http://from-section:8080"},
        },
    )
    recorder = install_session(monkeypatch, discovery("http://from-section:8080/api/oidc/token"))

    resolver = DelegationTokenEndpointResolver()
    await resolver.get_token_endpoint_async(TENANT_ID)

    assert recorder["urls"][0].startswith("http://from-section:8080/")


def test_ensure_configured_passes_with_only_the_frontend_runtime_section(monkeypatch):
    monkeypatch.setattr(
        endpoint_resolver,
        "get_configurations",
        lambda: {FRONTEND_RUNTIME_SECTION: {IAM_TOKEN_ENDPOINT_KEY: "http://blocks-iam:8080/api/oidc/token"}},
    )

    DelegationTokenEndpointResolver().ensure_configured()


async def test_prefers_the_environment_variable_over_configuration(monkeypatch):
    monkeypatch.setenv(IAM_BASE_URL_KEY, "http://from-env:8080")
    monkeypatch.setattr(
        endpoint_resolver,
        "get_configurations",
        lambda: {
            IAM_BASE_URL_KEY: "http://from-config:8080",
            FRONTEND_RUNTIME_SECTION: {IAM_BASE_URL_KEY: "http://from-section:8080"},
        },
    )
    recorder = install_session(monkeypatch, discovery("http://from-env:8080/api/oidc/token"))

    resolver = DelegationTokenEndpointResolver()
    await resolver.get_token_endpoint_async(TENANT_ID)

    assert recorder["urls"][0].startswith("http://from-env:8080/")


def test_settings_resolution_survives_configurations_never_being_loaded(monkeypatch):
    """A host may run on environment variables alone; that must not raise."""

    def unloaded():
        raise RuntimeError("Configurations not loaded")

    monkeypatch.setattr(endpoint_resolver, "get_configurations", unloaded)
    monkeypatch.setenv(IAM_TOKEN_ENDPOINT_KEY, "http://blocks-iam:8080/api/oidc/token")

    DelegationTokenEndpointResolver().ensure_configured()
