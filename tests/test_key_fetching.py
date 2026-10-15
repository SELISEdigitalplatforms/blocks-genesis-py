"""Card #44: signing keys and certificates are fetched safely.

DNS and HTTP are faked throughout: `key_fetch._resolve_addresses` stands in for DNS and
`aiohttp.ClientSession` for the network. Nothing here leaves the process.
"""
import asyncio
import builtins
import datetime
import json
import logging
import socket
import time
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import jwt
import pytest
import yarl
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi import HTTPException
from starlette.datastructures import URL

from blocks_genesis._auth import auth, key_fetch
from blocks_genesis._auth.key_fetch import JwksCache, KeyFetchError
from blocks_genesis._auth.third_party_provider import ThirdPartyJwtProvider
from blocks_genesis._tenant.tenant import Tenant

CFG = "blocks_genesis._core.configuration._app_configurations"
AUTH0 = "https://abc.eu.auth0.com/"
AUDIENCE = "api://klax"
JWKS_URL = "https://abc.eu.auth0.com/.well-known/jwks.json"
PUBLIC_IP = "93.184.216.34"


# ---------------------------------------------------------------------------
# Key material
# ---------------------------------------------------------------------------

def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _cert_pem(key, not_before, not_after):
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "abc.eu.auth0.com")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .sign(key, hashes.SHA256())
    )
    return certificate.public_bytes(serialization.Encoding.PEM)


@pytest.fixture(scope="module")
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def valid_pem(rsa_key):
    return _cert_pem(rsa_key, _now() - datetime.timedelta(days=1), _now() + datetime.timedelta(days=365))


@pytest.fixture(scope="module")
def expired_pem(rsa_key):
    return _cert_pem(rsa_key, _now() - datetime.timedelta(days=30), _now() - datetime.timedelta(days=1))


@pytest.fixture(scope="module")
def not_yet_valid_pem(rsa_key):
    return _cert_pem(rsa_key, _now() + datetime.timedelta(days=1), _now() + datetime.timedelta(days=30))


def _sign(key, kid="k1"):
    now = _now()
    claims = {
        "sub": "auth0|sara_123",
        "iss": AUTH0,
        "aud": AUDIENCE,
        "iat": now,
        "nbf": now,
        "exp": now + datetime.timedelta(minutes=30),
    }
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    headers = {"kid": kid} if kid is not None else None
    return jwt.encode(claims, private_pem, algorithm="RS256", headers=headers)


def _jwks(key, kid="k1", use="sig"):
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key(), as_dict=True)
    jwk.update(kid=kid, use=use)
    return json.dumps({"keys": [jwk]}).encode()


# ---------------------------------------------------------------------------
# Tenants, providers, caches
# ---------------------------------------------------------------------------

def _tenant():
    return Tenant(_id="ABC001", TenantId="ABC001", IsThirdPartyJwtEnabled=True)


def _primary_tenant(path):
    return Tenant(
        _id="ABC001",
        TenantId="ABC001",
        JwtTokenParameters={"PublicCertificatePath": path, "Issuer": "blocks", "Audiences": ["a"]},
    )


def _provider(**kwargs):
    defaults = dict(
        _id="p1", TenantId="ABC001", Key="auth0-main", IsActive=True,
        Issuer=AUTH0, Audiences=[AUDIENCE], Algorithms=[1],
        ClaimsMapping={"UserId": "sub"},
    )
    defaults.update(kwargs)
    return ThirdPartyJwtProvider(**defaults)


def _request():
    request = MagicMock()
    request.url = URL("https://api.klax.io/x")
    request.headers = {}
    request.query_params = {}
    return request


async def _validate(token, provider, cache=None):
    store = MagicMock()
    store.get_active = AsyncMock(return_value=[provider])
    with patch.object(auth, "get_third_party_provider_store", return_value=store):
        return await auth.validate_with_fallback(token, _tenant(), _request(), cache)


class _DictCache:
    def __init__(self):
        self.store = {}
        self.writes = []

    def get_bytes_value(self, key):
        return self.store.get(key)

    async def add_bytes_value_async(self, key, value, ttl):
        self.writes.append((key, value, ttl))
        self.store[key] = value


class _Clock:
    def __init__(self, start=1000.0):
        self.now = start

    def __call__(self):
        return self.now


# ---------------------------------------------------------------------------
# Fake DNS and HTTP
# ---------------------------------------------------------------------------

class _Content:
    def __init__(self, chunks):
        self._chunks = list(chunks)
        self.chunks_read = 0

    async def iter_chunked(self, size):
        for chunk in self._chunks:
            self.chunks_read += 1
            yield chunk


class _Response:
    def __init__(self, status=200, chunks=(b"key-bytes",), content_length=None,
                 headers=None, delay=0.0, error=None):
        self.status = status
        self.content = _Content(chunks)
        self.content_length = content_length
        self.headers = headers or {}
        self._delay = delay
        self._error = error

    async def __aenter__(self):
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._error is not None:
            raise self._error
        return self

    async def __aexit__(self, *exc):
        return False


class _Session:
    def __init__(self, http, kwargs):
        self._http = http
        self._kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        connector = self._kwargs.get("connector")
        if connector is not None:
            await connector.close()
        return False

    def get(self, url, **kwargs):
        self._http.requests.append((url, kwargs))
        return self._http.response


class _FakeHttp:
    """Stands in for aiohttp.ClientSession and records how it was built and used."""

    def __init__(self):
        self.response = _Response()
        self.sessions = []
        self.requests = []

    def __call__(self, **kwargs):
        self.sessions.append(kwargs)
        return _Session(self, kwargs)


def _family(ip):
    return socket.AF_INET6 if ":" in ip else socket.AF_INET


@pytest.fixture(autouse=True)
def not_local(monkeypatch):
    monkeypatch.setattr(CFG, None)


@pytest.fixture
def http(monkeypatch):
    fake = _FakeHttp()
    monkeypatch.setattr(key_fetch.aiohttp, "ClientSession", fake)
    return fake


@pytest.fixture
def dns(monkeypatch):
    resolve = AsyncMock(return_value=[(socket.AF_INET, PUBLIC_IP)])
    monkeypatch.setattr(key_fetch, "_resolve_addresses", resolve)
    return resolve


@pytest.fixture
def jwks_cache(monkeypatch):
    cache = JwksCache()
    monkeypatch.setattr(auth, "get_jwks_cache", lambda: cache)
    return cache


# ---------------------------------------------------------------------------
# The safe fetch
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("addresses", [
    ["169.254.169.254"], ["10.0.0.5"], ["172.16.0.1"], ["192.168.1.1"], ["127.0.0.1"],
    ["100.64.0.1"], ["0.0.0.0"], ["224.0.0.1"], ["240.0.0.1"], ["::1"], ["fe80::1%eth0"],
    ["fd00::1"], ["::ffff:127.0.0.1"], ["ff02::1"], ["fec0::1"], [PUBLIC_IP, "10.0.0.1"],
])
async def test_private_and_metadata_addresses_refused_before_any_request(addresses, dns, http):
    dns.return_value = [(_family(ip), ip) for ip in addresses]

    with pytest.raises(KeyFetchError) as err:
        await key_fetch.fetch_key_material("https://idp.example/jwks")

    assert err.value.reason == "address_not_allowed"
    assert http.sessions == []


@pytest.mark.parametrize("url", ["https://169.254.169.254/x", "https://[::1]/x"])
async def test_ip_literal_url_refused_without_dns(url, dns, http):
    with pytest.raises(KeyFetchError) as err:
        await key_fetch.fetch_key_material(url)

    assert err.value.reason == "address_not_allowed"
    dns.assert_not_awaited()
    assert http.sessions == []


async def test_public_address_is_pinned(dns, http):
    http.response = _Response(chunks=[b"abc", b"def"])

    body = await key_fetch.fetch_key_material("https://idp.example/jwks")

    assert body == b"abcdef"
    (session_kwargs,) = http.sessions
    assert session_kwargs["trust_env"] is False
    resolver = session_kwargs["connector"]._resolver
    assert isinstance(resolver, key_fetch._PinnedResolver)
    resolved = await resolver.resolve("idp.example", 443)
    assert [r["host"] for r in resolved] == [PUBLIC_IP]
    assert all(r["hostname"] == "idp.example" and r["port"] == 443 for r in resolved)
    with pytest.raises(OSError):
        await resolver.resolve("other.example", 443)
    ((url, get_kwargs),) = http.requests
    assert url == "https://idp.example/jwks"
    assert get_kwargs["allow_redirects"] is False


async def test_redirect_refused(dns, http):
    http.response = _Response(status=302, headers={"Location": "http://169.254.169.254/"})

    with pytest.raises(KeyFetchError) as err:
        await key_fetch.fetch_key_material("https://idp.example/jwks")

    assert err.value.reason == "redirect"
    assert len(http.requests) == 1


async def test_error_status_refused(dns, http):
    http.response = _Response(status=404)

    with pytest.raises(KeyFetchError) as err:
        await key_fetch.fetch_key_material("https://idp.example/jwks")

    assert err.value.reason == "status"


@pytest.mark.parametrize("configuration", [None, {"is_local": False}])
async def test_http_refused_outside_local(configuration, monkeypatch, dns, http):
    monkeypatch.setattr(CFG, configuration)

    with pytest.raises(KeyFetchError) as err:
        await key_fetch.fetch_key_material("http://idp.example/k")

    assert err.value.reason == "scheme_not_allowed"
    dns.assert_not_awaited()
    assert http.sessions == []


async def test_http_allowed_in_local_mode(monkeypatch, dns, http):
    monkeypatch.setattr(CFG, {"is_local": True})

    assert await key_fetch.fetch_key_material("http://idp.example/k") == b"key-bytes"
    dns.assert_awaited_once_with("idp.example", 80)


@pytest.mark.parametrize("url", [
    "file:///etc/passwd", "ftp://idp.example/k", "https://user:pw@idp.example/k",
    "https://idp.example:99999/k", "https:///k",
])
async def test_other_schemes_and_malformed_urls_refused(url, dns, http):
    with pytest.raises(KeyFetchError):
        await key_fetch.fetch_key_material(url)

    dns.assert_not_awaited()
    assert http.sessions == []


async def test_timeout(monkeypatch, dns, http):
    monkeypatch.setattr(key_fetch, "FETCH_TIMEOUT_SECONDS", 0.05)
    http.response = _Response(delay=10)

    started = time.monotonic()
    with pytest.raises(KeyFetchError) as err:
        await key_fetch.fetch_key_material("https://idp.example/jwks")

    assert err.value.reason == "timeout"
    assert time.monotonic() - started < 1


async def test_timeout_covers_dns(monkeypatch, dns, http):
    monkeypatch.setattr(key_fetch, "FETCH_TIMEOUT_SECONDS", 0.05)

    async def slow_dns(host, port):
        await asyncio.sleep(10)

    dns.side_effect = slow_dns

    with pytest.raises(KeyFetchError) as err:
        await key_fetch.fetch_key_material("https://idp.example/jwks")

    assert err.value.reason == "timeout"
    assert http.sessions == []


async def test_dns_failure(dns, http):
    dns.side_effect = socket.gaierror("no such host")

    with pytest.raises(KeyFetchError) as err:
        await key_fetch.fetch_key_material("https://idp.example/jwks")

    assert err.value.reason == "dns"
    assert http.sessions == []


@pytest.mark.parametrize("url", ["https://a..b/", f"https://{'a' * 64}.example/"])
async def test_an_invalid_host_name_is_a_dns_refusal(url, http, caplog):
    """The real resolver runs here: the name fails IDNA encoding before any lookup."""
    caplog.set_level(logging.INFO)

    with pytest.raises(KeyFetchError) as err:
        await key_fetch.fetch_key_material(url)

    assert err.value.reason == "dns"
    assert "Key fetch refused: dns" in caplog.text
    assert http.sessions == []


@pytest.mark.parametrize("url", [
    "https://bücher.example/jwks", "https://idp.example./jwks", "https://IDP.Example/jwks",
])
async def test_the_pinned_host_matches_the_name_aiohttp_connects_to(url, dns, http):
    await key_fetch.fetch_key_material(url)

    resolver = http.sessions[0]["connector"]._resolver
    resolved = await resolver.resolve(yarl.URL(url).raw_host, 443)
    assert [r["host"] for r in resolved] == [PUBLIC_IP]


async def test_size_cap_by_content_length(dns, http):
    http.response = _Response(content_length=key_fetch.MAX_RESPONSE_BYTES + 1)

    with pytest.raises(KeyFetchError) as err:
        await key_fetch.fetch_key_material("https://idp.example/jwks")

    assert err.value.reason == "too_large"
    assert http.response.content.chunks_read == 0


async def test_size_cap_while_streaming(dns, http):
    http.response = _Response(chunks=[b"x" * key_fetch.MAX_RESPONSE_BYTES, b"x"])

    with pytest.raises(KeyFetchError) as err:
        await key_fetch.fetch_key_material("https://idp.example/jwks")

    assert err.value.reason == "too_large"


async def test_error_log_names_reason_and_host_only(dns, http, caplog):
    caplog.set_level(logging.INFO)
    dns.return_value = [(socket.AF_INET, "10.0.0.1")]

    with pytest.raises(KeyFetchError) as err:
        await key_fetch.fetch_key_material("https://idp.example/jwks?sig=SECRET123")

    assert "address_not_allowed" in caplog.text
    assert "idp.example" in caplog.text
    assert "SECRET123" not in caplog.text
    assert "/jwks" not in caplog.text
    assert "SECRET123" not in str(err.value)
    assert "/jwks" not in str(err.value)
    assert err.value.__cause__ is None


async def test_connection_error_does_not_chain_the_url(dns, http):
    http.response = _Response(
        error=aiohttp.ClientConnectionError("https://idp.example/jwks?sig=SECRET123")
    )

    with pytest.raises(KeyFetchError) as err:
        await key_fetch.fetch_key_material("https://idp.example/jwks?sig=SECRET123")

    assert err.value.reason == "connect"
    assert "SECRET123" not in str(err.value)
    assert err.value.__cause__ is None


async def test_address_check_off_allows_a_private_address(dns, http):
    """Only the primary certificate, written by the platform, turns the check off."""
    dns.return_value = [(socket.AF_INET, "10.0.0.5")]

    body = await key_fetch.fetch_key_material("https://certs.internal/t.cer", check_address=False)

    assert body == b"key-bytes"
    resolver = http.sessions[0]["connector"]._resolver
    assert [r["host"] for r in await resolver.resolve("certs.internal", 443)] == ["10.0.0.5"]


async def test_address_check_off_still_refuses_http_and_redirects(dns, http):
    dns.return_value = [(socket.AF_INET, "10.0.0.5")]

    with pytest.raises(KeyFetchError) as err:
        await key_fetch.fetch_key_material("http://certs.internal/t.cer", check_address=False)
    assert err.value.reason == "scheme_not_allowed"

    http.response = _Response(status=301)
    with pytest.raises(KeyFetchError) as err:
        await key_fetch.fetch_key_material("https://certs.internal/t.cer", check_address=False)
    assert err.value.reason == "redirect"


# ---------------------------------------------------------------------------
# fetch_cert_bytes: who may use a file path
# ---------------------------------------------------------------------------

def _opened(spy, path):
    return any(call.args and str(call.args[0]) == str(path) for call in spy.call_args_list)


async def test_file_path_refused_for_third_party_provider(rsa_key, valid_pem, tmp_path, caplog):
    caplog.set_level(logging.INFO)
    path = tmp_path / "auth0.crt"
    path.write_bytes(valid_pem)
    provider = _provider(PublicCertificatePath=str(path))

    with patch("builtins.open", side_effect=builtins.open) as spy:
        result = await _validate(_sign(rsa_key), provider)

    assert result is None
    assert not _opened(spy, path)
    assert "third_party_certificate_unreadable" in caplog.text

    with patch("builtins.open", side_effect=builtins.open) as spy:
        with pytest.raises(KeyFetchError):
            await auth.fetch_cert_bytes("/etc/hosts")
    assert not _opened(spy, "/etc/hosts")


async def test_file_path_allowed_for_primary_tenant(valid_pem, tmp_path):
    path = tmp_path / "tenant.cer"
    path.write_bytes(valid_pem)
    cache = _DictCache()

    result = await auth.get_tenant_cert(cache, _primary_tenant(str(path)), "ABC001")

    assert result == valid_pem
    assert len(cache.writes) == 1


async def test_http_provider_certificate_refused_outside_local(rsa_key, dns, http):
    provider = _provider(PublicCertificatePath="http://abc.example/a.crt")

    assert await _validate(_sign(rsa_key), provider) is None
    dns.assert_not_awaited()
    assert http.sessions == []


async def test_provider_certificate_on_private_address_rejected_without_request(rsa_key, dns, http):
    dns.return_value = [(socket.AF_INET, "10.0.0.5")]
    provider = _provider(PublicCertificatePath="https://abc.example/a.crt")

    assert await _validate(_sign(rsa_key), provider) is None
    assert http.sessions == []


@pytest.mark.parametrize("configuration", [None, {"is_local": True}])
async def test_provider_jwks_on_metadata_address_rejected_without_request(
    configuration, rsa_key, monkeypatch, dns, http, jwks_cache
):
    monkeypatch.setattr(CFG, configuration)
    provider = _provider(JwksUrl="http://169.254.169.254/latest/meta-data/")

    assert await _validate(_sign(rsa_key), provider) is None
    assert http.sessions == []


async def test_primary_certificate_on_private_address_is_fetched(valid_pem, dns, http):
    dns.return_value = [(socket.AF_INET, "10.0.0.5")]
    http.response = _Response(chunks=[valid_pem])
    cache = _DictCache()

    result = await auth.get_tenant_cert(cache, _primary_tenant("https://certs.internal/t.cer"), "ABC001")

    assert result == valid_pem
    assert len(http.sessions) == 1


async def test_a_correctly_configured_certificate_provider_still_validates(rsa_key, valid_pem, dns, http):
    http.response = _Response(chunks=[valid_pem])
    provider = _provider(PublicCertificatePath="https://abc.example/a.crt")

    result = await _validate(_sign(rsa_key), provider)

    assert result is not None
    assert result.claims["sub"] == "auth0|sara_123"


# ---------------------------------------------------------------------------
# JWKS cache
# ---------------------------------------------------------------------------

def _slow_fetch(body):
    async def fetch(url):
        await asyncio.sleep(0.01)
        return body
    return AsyncMock(side_effect=fetch)


async def test_jwks_fetched_once_for_100_validations(rsa_key, monkeypatch, jwks_cache):
    fetch = _slow_fetch(_jwks(rsa_key))
    monkeypatch.setattr(key_fetch, "fetch_key_material", fetch)
    provider = _provider(JwksUrl=JWKS_URL)
    token = _sign(rsa_key)

    for _ in range(100):
        assert await _validate(token, provider) is not None
    fetch.assert_awaited_once()

    fetch.reset_mock()
    fresh = JwksCache()
    keys = await asyncio.gather(*(fresh.get_signing_key(JWKS_URL, token) for _ in range(100)))
    fetch.assert_awaited_once()
    assert all(k is keys[0] for k in keys)


async def test_unknown_kid_refreshes_at_most_once_a_minute(rsa_key, monkeypatch):
    fetch = AsyncMock(return_value=_jwks(rsa_key))
    monkeypatch.setattr(key_fetch, "fetch_key_material", fetch)
    clock = _Clock()
    cache = JwksCache(time_func=clock)

    await cache.get_signing_key(JWKS_URL, _sign(rsa_key, kid="k1"))
    assert fetch.await_count == 1

    clock.now += 60
    with pytest.raises(KeyFetchError) as err:
        await cache.get_signing_key(JWKS_URL, _sign(rsa_key, kid="zzz"))
    assert err.value.reason == "kid_not_found"
    assert fetch.await_count == 2

    clock.now += 30
    with pytest.raises(KeyFetchError) as err:
        await cache.get_signing_key(JWKS_URL, _sign(rsa_key, kid="zzz"))
    assert err.value.reason == "kid_not_found"
    assert fetch.await_count == 2

    clock.now += 31
    with pytest.raises(KeyFetchError):
        await cache.get_signing_key(JWKS_URL, _sign(rsa_key, kid="zzz"))
    assert fetch.await_count == 3


async def test_jwks_ttl_refresh(rsa_key, monkeypatch):
    fetch = AsyncMock(return_value=_jwks(rsa_key))
    monkeypatch.setattr(key_fetch, "fetch_key_material", fetch)
    clock = _Clock()
    cache = JwksCache(time_func=clock)
    token = _sign(rsa_key)
    start = clock.now

    await cache.get_signing_key(JWKS_URL, token)
    clock.now = start + 599
    await cache.get_signing_key(JWKS_URL, token)
    assert fetch.await_count == 1

    clock.now = start + 601
    await cache.get_signing_key(JWKS_URL, token)
    assert fetch.await_count == 2


async def test_jwks_failed_fetch_throttled(rsa_key, monkeypatch):
    fetch = AsyncMock(side_effect=KeyFetchError("connect", "abc.eu.auth0.com"))
    monkeypatch.setattr(key_fetch, "fetch_key_material", fetch)
    clock = _Clock()
    cache = JwksCache(time_func=clock)
    token = _sign(rsa_key)

    with pytest.raises(KeyFetchError) as err:
        await cache.get_signing_key(JWKS_URL, token)
    assert err.value.reason == "connect"

    clock.now += 10
    with pytest.raises(KeyFetchError) as err:
        await cache.get_signing_key(JWKS_URL, token)
    assert err.value.reason == "fetch_failed_recently"
    fetch.assert_awaited_once()


async def test_an_invalid_jwks_host_is_throttled_like_any_failed_fetch(rsa_key, http):
    clock = _Clock()
    cache = JwksCache(time_func=clock)
    token = _sign(rsa_key)

    with pytest.raises(KeyFetchError) as err:
        await cache.get_signing_key("https://a..b/jwks", token)
    assert err.value.reason == "dns"

    clock.now += 10
    with pytest.raises(KeyFetchError) as err:
        await cache.get_signing_key("https://a..b/jwks", token)
    assert err.value.reason == "fetch_failed_recently"


async def test_jwks_keeps_last_good_keys_on_failed_refresh(rsa_key, monkeypatch):
    fetch = AsyncMock(return_value=_jwks(rsa_key))
    monkeypatch.setattr(key_fetch, "fetch_key_material", fetch)
    clock = _Clock()
    cache = JwksCache(time_func=clock)
    token = _sign(rsa_key)
    start = clock.now

    key = await cache.get_signing_key(JWKS_URL, token)
    fetch.side_effect = KeyFetchError("connect", "abc.eu.auth0.com")
    clock.now = start + 601

    assert await cache.get_signing_key(JWKS_URL, token) is key
    assert fetch.await_count == 2


async def test_jwks_stale_keys_dropped_an_hour_past_ttl(rsa_key, monkeypatch):
    fetch = AsyncMock(return_value=_jwks(rsa_key))
    monkeypatch.setattr(key_fetch, "fetch_key_material", fetch)
    clock = _Clock()
    cache = JwksCache(time_func=clock)
    token = _sign(rsa_key)
    start = clock.now
    stale_limit = key_fetch.JWKS_CACHE_TTL_SECONDS + key_fetch.JWKS_STALE_MAX_SECONDS

    key = await cache.get_signing_key(JWKS_URL, token)
    fetch.side_effect = KeyFetchError("connect", "abc.eu.auth0.com")

    clock.now = start + 601
    assert await cache.get_signing_key(JWKS_URL, token) is key
    clock.now = start + stale_limit - 1
    assert await cache.get_signing_key(JWKS_URL, token) is key
    assert fetch.await_count == 3

    clock.now = start + stale_limit
    with pytest.raises(KeyFetchError) as err:
        await cache.get_signing_key(JWKS_URL, token)
    assert err.value.reason == "fetch_failed_recently"
    assert fetch.await_count == 3

    clock.now = start + stale_limit + 100
    with pytest.raises(KeyFetchError) as err:
        await cache.get_signing_key(JWKS_URL, token)
    assert err.value.reason == "connect"
    assert fetch.await_count == 4


async def test_token_without_kid_refused_without_fetch(rsa_key, monkeypatch):
    fetch = AsyncMock(return_value=_jwks(rsa_key))
    monkeypatch.setattr(key_fetch, "fetch_key_material", fetch)
    cache = JwksCache()

    with pytest.raises(KeyFetchError) as err:
        await cache.get_signing_key(JWKS_URL, _sign(rsa_key, kid=None))
    assert err.value.reason == "no_kid"

    with pytest.raises(KeyFetchError) as err:
        await cache.get_signing_key(JWKS_URL, "not-a-token")
    assert err.value.reason == "token_header"
    fetch.assert_not_awaited()


async def test_encryption_keys_are_not_used_for_signatures(rsa_key, monkeypatch):
    monkeypatch.setattr(key_fetch, "fetch_key_material", AsyncMock(return_value=_jwks(rsa_key, use="enc")))

    with pytest.raises(KeyFetchError) as err:
        await JwksCache().get_signing_key(JWKS_URL, _sign(rsa_key))
    assert err.value.reason == "kid_not_found"


@pytest.mark.parametrize("body", [b"not json", b"[1, 2]", b'{"keys": []}'])
async def test_an_unreadable_jwks_is_invalid(body, rsa_key, monkeypatch):
    monkeypatch.setattr(key_fetch, "fetch_key_material", AsyncMock(return_value=body))

    with pytest.raises(KeyFetchError) as err:
        await JwksCache().get_signing_key(JWKS_URL, _sign(rsa_key))
    assert err.value.reason == "invalid_jwks"


async def test_jwks_cache_bounded(rsa_key, monkeypatch):
    monkeypatch.setattr(key_fetch, "fetch_key_material", AsyncMock(return_value=_jwks(rsa_key)))
    cache = JwksCache()
    token = _sign(rsa_key)

    for i in range(key_fetch.JWKS_CACHE_MAX_URLS + 1):
        await cache.get_signing_key(f"https://idp{i}.example/jwks", token)

    assert len(cache._entries) <= key_fetch.JWKS_CACHE_MAX_URLS


def test_get_jwks_cache_is_a_process_singleton():
    assert key_fetch.get_jwks_cache() is key_fetch.get_jwks_cache()


def test_auth_has_no_pyjwkclient():
    assert not hasattr(auth, "PyJWKClient")


# ---------------------------------------------------------------------------
# Certificate validity and cache keys
# ---------------------------------------------------------------------------

async def test_expired_provider_certificate_rejected(rsa_key, expired_pem, caplog):
    caplog.set_level(logging.INFO)
    cache = _DictCache()
    provider = _provider(PublicCertificatePath="https://abc.example/a.crt")

    with patch.object(auth, "fetch_cert_bytes", new_callable=AsyncMock, return_value=expired_pem):
        assert await _validate(_sign(rsa_key), provider, cache) is None

    assert "third_party_certificate_not_valid" in caplog.text
    assert cache.writes == []


async def test_not_yet_valid_provider_certificate_rejected(rsa_key, not_yet_valid_pem):
    provider = _provider(PublicCertificatePath="https://abc.example/a.crt")

    with patch.object(auth, "fetch_cert_bytes", new_callable=AsyncMock, return_value=not_yet_valid_pem):
        assert await _validate(_sign(rsa_key), provider) is None


async def test_cached_expired_provider_certificate_rejected(rsa_key, expired_pem):
    cache = MagicMock()
    cache.get_bytes_value.return_value = expired_pem
    cache.add_bytes_value_async = AsyncMock()
    provider = _provider(PublicCertificatePath="https://abc.example/a.crt")

    with patch.object(auth, "fetch_cert_bytes", new_callable=AsyncMock) as fetch:
        assert await _validate(_sign(rsa_key), provider, cache) is None
    fetch.assert_not_awaited()


async def test_expired_primary_certificate_not_returned_or_cached(expired_pem, caplog):
    caplog.set_level(logging.INFO)
    cache = MagicMock()
    cache.get_bytes_value.return_value = None
    cache.add_bytes_value_async = AsyncMock()

    with patch.object(auth, "fetch_cert_bytes", new_callable=AsyncMock, return_value=expired_pem):
        result = await auth.get_tenant_cert(cache, _primary_tenant("https://certs.example/t.cer"), "ABC001")

    assert result is None
    cache.add_bytes_value_async.assert_not_awaited()
    assert "tenant_certificate_not_valid" in caplog.text


async def test_validate_jwt_token_refuses_expired_cached_certificate(expired_pem):
    tenant = _primary_tenant("https://certs.example/t.cer")

    with patch.object(auth, "get_tenant_cert", new_callable=AsyncMock, return_value=expired_pem):
        with pytest.raises(HTTPException) as err:
            await auth.validate_jwt_token("tok", tenant, MagicMock(), _request())

    assert err.value.status_code == 500


async def test_primary_cache_ttl_capped(rsa_key, valid_pem):
    tenant = _primary_tenant("https://certs.example/t.cer")

    cache = _DictCache()
    with patch.object(auth, "fetch_cert_bytes", new_callable=AsyncMock, return_value=valid_pem):
        await auth.get_tenant_cert(cache, tenant, "ABC001")
    assert cache.writes[0][2] == 3600

    short = _cert_pem(rsa_key, _now() - datetime.timedelta(days=1), _now() + datetime.timedelta(seconds=120))
    cache = _DictCache()
    with patch.object(auth, "fetch_cert_bytes", new_callable=AsyncMock, return_value=short):
        await auth.get_tenant_cert(cache, tenant, "ABC001")
    assert 1 <= cache.writes[0][2] <= 120


async def test_changed_primary_path_not_served_from_cache(valid_pem):
    cache = _DictCache()
    path_a = "https://certs.example/tenant-a.cer"
    path_b = "https://certs.example/tenant-b.cer"

    with patch.object(auth, "fetch_cert_bytes", new_callable=AsyncMock, return_value=valid_pem) as fetch:
        await auth.get_tenant_cert(cache, _primary_tenant(path_a), "ABC001")
        await auth.get_tenant_cert(cache, _primary_tenant(path_b), "ABC001")

    assert fetch.await_count == 2
    assert fetch.await_args.args[0] == path_b
    key_a, key_b = (write[0] for write in cache.writes)
    assert key_a != key_b
    assert path_a not in key_a and path_b not in key_b


async def test_changed_provider_path_not_served_from_cache(rsa_key, valid_pem):
    cache = _DictCache()
    path_a = "https://abc.example/a.crt"
    path_b = "https://abc.example/b.crt"

    with patch.object(auth, "fetch_cert_bytes", new_callable=AsyncMock, return_value=valid_pem) as fetch:
        assert await _validate(_sign(rsa_key), _provider(PublicCertificatePath=path_a), cache) is not None
        assert await _validate(_sign(rsa_key), _provider(PublicCertificatePath=path_b), cache) is not None

    assert fetch.await_count == 2
    assert fetch.await_args.args[0] == path_b
    key_a, key_b = (write[0] for write in cache.writes)
    assert key_a.startswith("tpprovcert::ABC001::auth0-main::")
    assert key_a != key_b
    assert path_a not in key_a and path_b not in key_b
