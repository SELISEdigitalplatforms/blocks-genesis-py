import importlib
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.requests import Request as StarletteRequest

from blocks_genesis._auth import auth
from blocks_genesis._auth.blocks_context import BlocksContextManager
from blocks_genesis._core import api, configuration
from blocks_genesis._middlewares.tenant_middleware import TenantValidationMiddleware
from blocks_genesis._tenant.tenant import Tenant

A = "blocks_genesis._core.api."
CFG = "blocks_genesis._core.configuration._app_configurations"
TENANT_SINGLETON = "blocks_genesis._tenant.tenant_service._tenant_service"
OP = "blocks_genesis._middlewares.origin_policy"
ENV_KEYS = ("BLOCKS_CORS_ALLOWED_ORIGINS", "BLOCKS_TRUSTED_PROXY_HOSTS")
ALL_METHODS = "DELETE, GET, HEAD, OPTIONS, PATCH, POST, PUT"


@pytest.fixture(autouse=True)
def _clean_settings(monkeypatch):
    monkeypatch.setattr(CFG, None)
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    # The domain-lookup throttle is process-wide; give every test a fresh one.
    monkeypatch.setattr(OP + "._lookup_throttle", {"window_start": None, "count": 0, "logged_at": None}, raising=False)


def _tenant(domain="https://tenant.example.com", disabled=False, cookie_key=""):
    return Tenant(
        _id="t1", TenantId="t1", IsDisabled=disabled,
        Applications=[{"Domain": domain}],
        ThirdPartyJwtTokenParameters={"CookieKey": cookie_key} if cookie_key else None,
    )


def _lookup(monkeypatch, tenant=None):
    """Install a tenant service singleton whose domain lookup returns `tenant`."""
    service = MagicMock()
    service.get_tenant_by_domain = AsyncMock(return_value=tenant)
    monkeypatch.setattr(TENANT_SINGLETON, service)
    return service.get_tenant_by_domain


def _app(cors_origins=None):
    with patch(A + "FastAPIInstrumentor"):
        app = FastAPI()
        if cors_origins is None:
            api.configure_genesis(app)
        else:
            api.configure_genesis(app, cors_origins=cors_origins)

    @app.get("/api/x")
    async def api_x():
        return {"ok": True}

    @app.get("/whoami")
    async def whoami(request: Request):
        return {"host": request.client.host}

    return app


def _preflight(client, origin, path="/api/x", headers=None):
    h = {"Origin": origin, "Access-Control-Request-Method": "GET"}
    h.update(headers or {})
    return client.options(path, headers=h)


def _assert_echoed(response, origin):
    assert response.headers.get("access-control-allow-origin") == origin
    assert response.headers.get("access-control-allow-credentials") == "true"
    assert "origin" in response.headers.get("vary", "").lower()


def _assert_refused(response):
    assert "access-control-allow-origin" not in response.headers
    assert "access-control-allow-credentials" not in response.headers


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------

def test_preflight_from_unlisted_origin_gets_no_allow_origin(monkeypatch):
    _lookup(monkeypatch, None)
    with TestClient(_app()) as client:
        response = _preflight(client, "https://evil.example")
    assert response.status_code == 400
    _assert_refused(response)


def test_simple_request_from_unlisted_origin_gets_no_allow_origin(monkeypatch):
    _lookup(monkeypatch, None)
    with TestClient(_app()) as client:
        response = client.get("/ping", headers={"Origin": "https://evil.example"})
    assert response.status_code == 200
    _assert_refused(response)


def test_allow_listed_origin_is_echoed_with_credentials(monkeypatch):
    lookup = _lookup(monkeypatch, None)
    with TestClient(_app(cors_origins=["https://app.example.com/"])) as client:
        pre = _preflight(client, "https://app.example.com")
        simple = client.get("/ping", headers={"Origin": "https://app.example.com"})
    assert pre.status_code == 200
    _assert_echoed(pre, "https://app.example.com")
    assert simple.status_code == 200
    _assert_echoed(simple, "https://app.example.com")
    lookup.assert_not_awaited()


def test_allow_list_from_configuration_key(monkeypatch):
    _lookup(monkeypatch, None)
    monkeypatch.setenv("BLOCKS_CORS_ALLOWED_ORIGINS", "https://a.example, https://b.example")
    with TestClient(_app()) as client:
        for origin in ("https://a.example", "https://b.example"):
            _assert_echoed(client.get("/ping", headers={"Origin": origin}), origin)

    monkeypatch.delenv("BLOCKS_CORS_ALLOWED_ORIGINS")
    monkeypatch.setattr(CFG, {"BLOCKS_CORS_ALLOWED_ORIGINS": ["https://a.example", "https://b.example"]})
    with TestClient(_app()) as client:
        for origin in ("https://a.example", "https://b.example"):
            _assert_echoed(client.get("/ping", headers={"Origin": origin}), origin)


def test_tenant_domain_origin_is_echoed_with_credentials(monkeypatch):
    lookup = _lookup(monkeypatch, _tenant())
    with TestClient(_app()) as client:
        pre = _preflight(client, "https://tenant.example.com")
        simple = client.get("/ping", headers={"Origin": "https://tenant.example.com"})
    assert pre.status_code == 200
    _assert_echoed(pre, "https://tenant.example.com")
    _assert_echoed(simple, "https://tenant.example.com")
    lookup.assert_awaited_with("tenant.example.com")


def test_disabled_tenant_domain_is_refused(monkeypatch):
    _lookup(monkeypatch, _tenant(disabled=True))
    with TestClient(_app()) as client:
        pre = _preflight(client, "https://tenant.example.com")
        simple = client.get("/ping", headers={"Origin": "https://tenant.example.com"})
    assert pre.status_code == 400
    _assert_refused(pre)
    _assert_refused(simple)


@pytest.mark.parametrize("config", [None, {"is_local": False}])
def test_localhost_origin_refused_outside_local_mode(monkeypatch, config):
    lookup = _lookup(monkeypatch, _tenant(domain="http://localhost:3000"))
    monkeypatch.setattr(CFG, config)
    with TestClient(_app()) as client:
        pre = _preflight(client, "http://localhost:3000")
        simple = client.get("/ping", headers={"Origin": "http://localhost:3000"})
    _assert_refused(pre)
    _assert_refused(simple)
    lookup.assert_not_awaited()


def test_localhost_origin_allowed_in_local_mode(monkeypatch):
    _lookup(monkeypatch, None)
    monkeypatch.setattr(CFG, {"is_local": True})
    with TestClient(_app()) as client:
        pre = _preflight(client, "http://localhost:3000")
        simple = client.get("/ping", headers={"Origin": "http://localhost:3000"})
    assert pre.status_code == 200
    _assert_echoed(pre, "http://localhost:3000")
    _assert_echoed(simple, "http://localhost:3000")


def test_preflight_keeps_methods_and_headers(monkeypatch):
    _lookup(monkeypatch, None)
    with TestClient(_app(cors_origins=["https://app.example.com"])) as client:
        response = _preflight(
            client, "https://app.example.com",
            headers={"Access-Control-Request-Headers": "x-blocks-key, authorization"},
        )
    assert response.status_code == 200
    assert response.headers["access-control-allow-headers"] == "x-blocks-key, authorization"
    assert response.headers["access-control-allow-methods"] == ALL_METHODS


def _vary_tokens(response):
    return [v.strip().lower() for v in response.headers.get("vary", "").split(",") if v.strip()]


def test_every_response_varies_on_origin(monkeypatch):
    _lookup(monkeypatch, None)
    with TestClient(_app(cors_origins=["https://app.example.com"])) as client:
        responses = {
            "no origin": client.get("/ping"),
            "refused simple": client.get("/ping", headers={"Origin": "https://evil.example"}),
            "refused preflight": _preflight(client, "https://evil.example"),
            "allowed simple": client.get("/ping", headers={"Origin": "https://app.example.com"}),
            "allowed preflight": _preflight(client, "https://app.example.com"),
        }
    for name, response in responses.items():
        assert _vary_tokens(response).count("origin") == 1, (name, response.headers.get("vary"))


# ---------------------------------------------------------------------------
# Origin policy
# ---------------------------------------------------------------------------

def _policy_module():
    return importlib.import_module("blocks_genesis._middlewares.origin_policy")


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


@pytest.mark.asyncio
async def test_malformed_origins_refused_without_lookup(monkeypatch):
    lookup = _lookup(monkeypatch, _tenant(domain="https://a.example"))
    policy = _policy_module().OriginPolicy()
    for origin in ("null", "file://x", "https://a.example/path", "https://u@a.example",
                   "ftp://a.example", "http://[::1"):
        assert await policy.is_allowed(origin) is False, origin
    lookup.assert_not_awaited()


@pytest.mark.asyncio
async def test_domain_lookup_is_cached(monkeypatch):
    lookup = _lookup(monkeypatch, _tenant())
    clock = _Clock()
    policy = _policy_module().OriginPolicy(time_func=clock)
    assert await policy.is_allowed("https://tenant.example.com") is True
    assert await policy.is_allowed("https://tenant.example.com") is True
    assert lookup.await_count == 1
    clock.now += 61
    assert await policy.is_allowed("https://tenant.example.com") is True
    assert lookup.await_count == 2

    lookup.return_value = None
    assert await policy.is_allowed("https://other.example") is False
    assert await policy.is_allowed("https://other.example") is False
    assert lookup.await_count == 3


@pytest.mark.asyncio
async def test_domain_cache_is_bounded(monkeypatch):
    _lookup(monkeypatch, None)
    module = _policy_module()
    clock = _Clock()
    policy = module.OriginPolicy(time_func=clock)
    for i in range(module._DOMAIN_CACHE_MAX_ENTRIES + 5):
        clock.now += 0.1  # stay under the lookup throttle
        await policy.is_allowed(f"https://h{i}.example")
    assert len(policy._cache) <= module._DOMAIN_CACHE_MAX_ENTRIES


@pytest.mark.asyncio
async def test_domain_cache_evicts_expired_then_oldest(monkeypatch):
    allowed_hosts = {"a.example", "c.example", "d.example", "e.example"}
    lookup = _lookup(monkeypatch)
    lookup.side_effect = lambda host: _tenant(domain=f"https://{host}") if host in allowed_hosts else None
    module = _policy_module()
    monkeypatch.setattr(module, "_DOMAIN_CACHE_MAX_ENTRIES", 3)
    clock = _Clock()
    policy = module.OriginPolicy(time_func=clock)
    for host in ("a", "b", "c"):
        await policy.is_allowed(f"https://{host}.example")

    clock.now += 11  # the negative entry for b has expired, a and c have not
    await policy.is_allowed("https://d.example")
    assert list(policy._cache) == ["a.example", "c.example", "d.example"]

    await policy.is_allowed("https://e.example")  # nothing expired: the oldest goes
    assert list(policy._cache) == ["c.example", "d.example", "e.example"]


@pytest.mark.asyncio
async def test_negative_result_cached_for_shorter_time(monkeypatch):
    lookup = _lookup(monkeypatch, None)
    clock = _Clock()
    policy = _policy_module().OriginPolicy(time_func=clock)
    assert await policy.is_allowed("https://blip.example") is False
    clock.now += 9
    assert await policy.is_allowed("https://blip.example") is False
    assert lookup.await_count == 1

    lookup.return_value = _tenant(domain="https://blip.example")
    clock.now += 2
    assert await policy.is_allowed("https://blip.example") is True
    assert lookup.await_count == 2

    clock.now += 30  # a positive result is still cached
    assert await policy.is_allowed("https://blip.example") is True
    assert lookup.await_count == 2


def _throttle_warnings(caplog):
    return [r for r in caplog.records if r.levelno == logging.WARNING and r.name == OP]


@pytest.mark.asyncio
async def test_uncached_lookups_are_throttled(monkeypatch, caplog):
    lookup = _lookup(monkeypatch, _tenant())
    module = _policy_module()
    limit = module._DOMAIN_LOOKUPS_PER_SECOND
    clock = _Clock()
    policy = module.OriginPolicy(time_func=clock)
    other_policy = module.OriginPolicy(time_func=clock)

    with caplog.at_level(logging.WARNING, logger=OP):
        for i in range(limit):
            assert await policy.is_allowed(f"https://h{i}.example") is True
        # Over the limit, process-wide: refused, not looked up, not cached, logged once.
        assert await other_policy.is_allowed("https://late.example") is False
        assert await policy.is_allowed("https://later.example") is False
        assert lookup.await_count == limit
        assert "late.example" not in other_policy._cache
        assert len(_throttle_warnings(caplog)) == 1
        # Cached hosts are still answered.
        assert await policy.is_allowed("https://h0.example") is True

        clock.now += 1.01
        assert await other_policy.is_allowed("https://late.example") is True
        assert lookup.await_count == limit + 1

        for i in range(limit):
            await policy.is_allowed(f"https://burst{i}.example")
        assert len(_throttle_warnings(caplog)) == 1  # still within the minute

        clock.now += 61
        for i in range(limit + 1):
            await policy.is_allowed(f"https://again{i}.example")
        assert len(_throttle_warnings(caplog)) == 2


@pytest.mark.asyncio
async def test_uninitialised_tenant_service_refuses_and_is_not_cached(monkeypatch):
    monkeypatch.setattr(TENANT_SINGLETON, None)
    policy = _policy_module().OriginPolicy()
    assert await policy.is_allowed("https://tenant.example.com") is False

    lookup = _lookup(monkeypatch, _tenant())
    assert await policy.is_allowed("https://tenant.example.com") is True
    lookup.assert_awaited_once()


# ---------------------------------------------------------------------------
# Tenant origin check on /api
# ---------------------------------------------------------------------------

def _scope_request(headers=None, method="GET", path="/api/x"):
    raw = [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in (headers or {}).items()]
    return StarletteRequest({
        "type": "http", "method": method, "path": path, "raw_path": path.encode(),
        "query_string": b"", "headers": raw, "scheme": "https",
        "server": ("api.example.com", 443), "client": ("1.2.3.4", 5000),
    })


async def _dispatch(monkeypatch, headers):
    service = MagicMock()
    service.get_tenant = AsyncMock(return_value=_tenant(domain="https://app.example.com"))
    monkeypatch.setattr("blocks_genesis._middlewares.tenant_middleware.get_tenant_service", lambda: service)
    call_next = AsyncMock(return_value=MagicMock(status_code=200, headers={}, body_iterator=None))
    middleware = TenantValidationMiddleware(MagicMock(), included_paths=["/api"])
    with patch("blocks_genesis._middlewares.tenant_middleware.Activity"):
        response = await middleware.dispatch(_scope_request(headers), call_next)
    return response, call_next


@pytest.mark.asyncio
async def test_api_localhost_origin_outside_local_gets_406(monkeypatch):
    monkeypatch.setattr(CFG, {"is_local": False})
    response, call_next = await _dispatch(monkeypatch, {"x-blocks-key": "t1", "Origin": "http://localhost:3000"})
    assert response.status_code == 406
    call_next.assert_not_awaited()


@pytest.mark.asyncio
async def test_api_localhost_origin_in_local_mode_passes(monkeypatch):
    monkeypatch.setattr(CFG, {"is_local": True})
    _, call_next = await _dispatch(monkeypatch, {"x-blocks-key": "t1", "Origin": "http://localhost:3000"})
    call_next.assert_awaited_once()


@pytest.mark.asyncio
async def test_api_missing_origin_still_passes(monkeypatch):
    _, call_next = await _dispatch(monkeypatch, {"x-blocks-key": "t1"})
    call_next.assert_awaited_once()


@pytest.mark.asyncio
async def test_api_tenant_origin_still_passes(monkeypatch):
    _, call_next = await _dispatch(monkeypatch, {"x-blocks-key": "t1", "Origin": "https://app.example.com"})
    call_next.assert_awaited_once()


# ---------------------------------------------------------------------------
# Cookie tokens
# ---------------------------------------------------------------------------

def _cookie_request(cookies, headers=None, method="POST"):
    h = dict(headers or {})
    if cookies:
        h["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
    return _scope_request(h, method=method)


def _cookie_service():
    service = MagicMock()
    service.get_tenant = AsyncMock(return_value=_tenant(domain="https://app.example.com", cookie_key="ckey"))
    return service


@pytest.fixture
def tenant_context(monkeypatch):
    ctx = MagicMock(tenant_id="t1")
    monkeypatch.setattr(BlocksContextManager, "get_context", staticmethod(lambda *a, **k: ctx))
    return ctx


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
async def test_cookie_not_read_without_origin_or_referer(tenant_context, method):
    service = _cookie_service()
    cookies = {"app.example.com": "tok", "ckey": "tp"}
    assert await auth._extract_token_from_cookie(_cookie_request(cookies, method=method), service) == (None, False, None)

    bearer = _cookie_request(cookies, {"Authorization": "Bearer abc"}, method=method)
    assert await auth.extract_token_from_request(bearer, service) == ("abc", False, None)


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS"])
async def test_safe_method_cookie_read_without_origin_or_referer(tenant_context, method):
    service = _cookie_service()
    request = _cookie_request({"ckey": "tp"}, method=method)
    assert await auth._extract_token_from_cookie(request, service) == ("tp", True, None)


@pytest.mark.asyncio
async def test_provider_cookie_read_with_matching_origin(tenant_context):
    request = _cookie_request({"ckey": "tp"}, {"Origin": "https://app.example.com"})
    assert await auth._extract_token_from_cookie(request, _cookie_service()) == ("tp", True, "app.example.com")


@pytest.mark.asyncio
async def test_tenant_cookie_read_with_matching_referer(tenant_context):
    request = _cookie_request({"app.example.com": "tok"}, {"Referer": "https://app.example.com/page"})
    assert await auth._extract_token_from_cookie(request, _cookie_service()) == ("tok", False, "app.example.com")


@pytest.mark.asyncio
async def test_cookie_not_read_for_foreign_origin(tenant_context):
    request = _cookie_request({"other.example": "tok", "ckey": "tp"}, {"Origin": "https://other.example"})
    assert await auth._extract_token_from_cookie(request, _cookie_service()) == (None, False, None)


@pytest.mark.asyncio
async def test_trailing_dot_origin_matches_tenant_domain(tenant_context, monkeypatch):
    request = _cookie_request({"app.example.com": "tok"}, {"Origin": "https://app.example.com."})
    assert await auth._extract_token_from_cookie(request, _cookie_service()) == ("tok", False, "app.example.com")

    _, call_next = await _dispatch(monkeypatch, {"x-blocks-key": "t1", "Origin": "https://app.example.com."})
    call_next.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["POST", "GET"])
async def test_localhost_alias_cookie_only_in_local_mode(tenant_context, monkeypatch, method):
    request = _cookie_request({"127.0.0.1": "tok"}, {"Origin": "http://localhost:3000"}, method=method)
    monkeypatch.setattr(CFG, {"is_local": False})
    assert await auth._extract_token_from_cookie(request, _cookie_service()) == (None, False, None)
    monkeypatch.setattr(CFG, {"is_local": True})
    assert await auth._extract_token_from_cookie(request, _cookie_service()) == ("tok", False, "localhost")


# ---------------------------------------------------------------------------
# Proxy headers
# ---------------------------------------------------------------------------

def _proxy_kwargs(app):
    entries = [m for m in app.user_middleware if m.cls.__name__ == "ProxyHeadersMiddleware"]
    assert len(entries) == 1
    return entries[0].kwargs


def _proxy_warnings(caplog):
    return [r for r in caplog.records
            if r.levelno == logging.WARNING and "BLOCKS_TRUSTED_PROXY_HOSTS" in r.getMessage()]


def test_proxy_default_trusts_all_and_warns_outside_local(caplog):
    with caplog.at_level(logging.WARNING):
        app = _app()
    assert _proxy_kwargs(app)["trusted_hosts"] == "*"
    assert len(_proxy_warnings(caplog)) == 1


def test_proxy_default_no_warning_in_local_mode(monkeypatch, caplog):
    monkeypatch.setattr(CFG, {"is_local": True})
    with caplog.at_level(logging.WARNING):
        app = _app()
    assert _proxy_kwargs(app)["trusted_hosts"] == "*"
    assert _proxy_warnings(caplog) == []


def test_proxy_hosts_from_configuration(monkeypatch, caplog):
    monkeypatch.setattr(CFG, {"BLOCKS_TRUSTED_PROXY_HOSTS": ["10.0.0.0/8", "127.0.0.1"]})
    with caplog.at_level(logging.WARNING):
        app = _app()
    assert _proxy_kwargs(app)["trusted_hosts"] == ["10.0.0.0/8", "127.0.0.1"]
    assert _proxy_warnings(caplog) == []


def test_forwarded_for_from_untrusted_host_is_ignored(monkeypatch):
    monkeypatch.setenv("BLOCKS_TRUSTED_PROXY_HOSTS", "10.0.0.1")
    with TestClient(_app()) as client:
        assert client.get("/whoami", headers={"X-Forwarded-For": "1.2.3.4"}).json()["host"] == "testclient"

    monkeypatch.delenv("BLOCKS_TRUSTED_PROXY_HOSTS")
    with TestClient(_app()) as client:
        assert client.get("/whoami", headers={"X-Forwarded-For": "1.2.3.4"}).json()["host"] == "1.2.3.4"


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------

def test_is_local_deployment(monkeypatch):
    for value, expected in ((None, False), ({}, False), ({"is_local": False}, False),
                            ({"is_local": "false"}, False), ({"is_local": True}, True),
                            ({"is_local": "true"}, True), ({"is_local": " True "}, True)):
        monkeypatch.setattr(CFG, value)
        assert configuration.is_local_deployment() is expected, value


def test_get_setting_list(monkeypatch):
    assert configuration.get_setting_list("BLOCKS_CORS_ALLOWED_ORIGINS") == []

    monkeypatch.setattr(CFG, {"BLOCKS_CORS_ALLOWED_ORIGINS": ["https://a.example", " ", "https://b.example "]})
    assert configuration.get_setting_list("BLOCKS_CORS_ALLOWED_ORIGINS") == ["https://a.example", "https://b.example"]

    monkeypatch.setattr(CFG, {"BLOCKS_CORS_ALLOWED_ORIGINS": "https://a.example, ,https://b.example"})
    assert configuration.get_setting_list("BLOCKS_CORS_ALLOWED_ORIGINS") == ["https://a.example", "https://b.example"]

    monkeypatch.setenv("BLOCKS_CORS_ALLOWED_ORIGINS", "https://env.example")
    assert configuration.get_setting_list("BLOCKS_CORS_ALLOWED_ORIGINS") == ["https://env.example"]

    assert configuration.get_setting_list("BLOCKS_SOMETHING_ELSE") == []
