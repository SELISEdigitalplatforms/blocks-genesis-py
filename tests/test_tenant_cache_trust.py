"""Card #41: tenant settings and signing certificates are not trusted from the cache.

A tenant update on the cache channel only names a tenant; the tenant is re-read from
the root database. Signing certificates are cached in process, never in Redis.
"""
import asyncio
import datetime
import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from jwt import InvalidTokenError
from starlette.datastructures import URL

from blocks_genesis._auth import auth
from blocks_genesis._auth.third_party_provider import ThirdPartyJwtProvider
from blocks_genesis._tenant.tenant import Tenant
from blocks_genesis._tenant.tenant_service import TenantCacheUpdateMessage, TenantService

TENANT_LOGGER = "blocks_genesis._tenant.tenant_service"
REAL_CERT_PATH = "https://certs.example/real.cer"
PROVIDER_CERT_PATH = "https://abc.example/auth0.crt"
AUTH0 = "https://abc.eu.auth0.com/"
AUDIENCE = "api://klax"


# ---------------------------------------------------------------------------
# Tenant service
# ---------------------------------------------------------------------------

def _svc(find_one=None):
    s = TenantService.__new__(TenantService)
    s.cache = MagicMock()
    s.database = MagicMock()
    s.database.__getitem__.return_value.find_one = find_one or AsyncMock(return_value=None)
    s._tenant_cache = {}
    s._root_tenant_id = None
    s._tenant_load_in_progress = {}
    s._update_channel = "tenant::updates"
    s._collection_name = "Tenants"
    s._initialized = False
    s._is_subscribed = False
    s._disposed = False
    s._initialize_lock = asyncio.Lock()
    s._tenant_load_lock = asyncio.Lock()
    s._tenant_loaded_at = {}
    s._unknown_tenants = {}
    s._unknown_domains = {}
    return s


def _doc(tid="t1", **overrides):
    doc = {
        "_id": tid,
        "TenantId": tid,
        "DBName": "db",
        "DbConnectionString": "mongodb://real",
        "IsRootTenant": False,
        "TenantSalt": "real-salt",
        "JwtTokenParameters": {"PublicCertificatePath": REAL_CERT_PATH},
    }
    doc.update(overrides)
    return doc


def _upsert(tid="t1", **overrides):
    return TenantCacheUpdateMessage(
        action="upsert", tenant=Tenant(**_doc(tid, **overrides))
    ).model_dump_json(by_alias=True)


def _remove(tid="t1"):
    return TenantCacheUpdateMessage(action="remove", tenant_id=tid).model_dump_json(by_alias=True)


def _t(tid="t1"):
    return Tenant(_id=tid, TenantId=tid, DBName="db", DbConnectionString="conn")


@pytest.mark.parametrize(
    "overrides, read",
    [
        ({"DbConnectionString": "mongodb://evil"}, lambda t: t.db_connection_string),
        ({"IsRootTenant": True}, lambda t: t.is_root_tenant),
        ({"TenantSalt": "evil-salt"}, lambda t: t.tenant_salt),
        (
            {"JwtTokenParameters": {"PublicCertificatePath": "https://evil.example/c.cer"}},
            lambda t: t.jwt_token_parameters.public_certificate_path,
        ),
    ],
    ids=["connection_string", "root_flag", "salt", "certificate_path"],
)
async def test_forged_upsert_fields_do_not_reach_cache(overrides, read):
    find_one = AsyncMock(return_value=_doc())
    s = _svc(find_one)
    s._tenant_cache["t1"] = Tenant(**_doc())
    real = read(s._tenant_cache["t1"])

    await s._handle_update(_upsert(**overrides))

    assert read(s._tenant_cache["t1"]) == real
    assert await s.get_db_connection("t1") == ("db", "mongodb://real")
    find_one.assert_awaited_with({"$or": [{"_id": "t1"}, {"TenantId": "t1"}]})
    assert s.get_root_tenant_id() is None


async def test_upsert_applies_database_version():
    s = _svc(AsyncMock(return_value=_doc(DbConnectionString="mongodb://new")))
    s._tenant_cache["t1"] = Tenant(**_doc(DbConnectionString="mongodb://old"))

    await s._handle_update(_upsert(DbConnectionString="anything"))

    assert s._tenant_cache["t1"].db_connection_string == "mongodb://new"


async def test_forged_upsert_for_unknown_tenant_adds_nothing():
    s = _svc(AsyncMock(return_value=None))

    await s._handle_update(_upsert("ghost"))

    assert "ghost" not in s._tenant_cache


async def test_upsert_for_tenant_missing_from_database_removes_cached_copy():
    s = _svc(AsyncMock(return_value=None))
    s._tenant_cache["t1"] = Tenant(**_doc())

    await s._handle_update(_upsert())

    assert "t1" not in s._tenant_cache


async def test_upsert_for_tenant_disabled_in_database_removes_it():
    s = _svc(AsyncMock(return_value=_doc(IsDisabled=True)))
    s._tenant_cache["t1"] = Tenant(**_doc())

    await s._handle_update(_upsert(IsDisabled=False))

    assert "t1" not in s._tenant_cache


async def test_remove_message_removes_without_database_read():
    find_one = AsyncMock(return_value=_doc())
    s = _svc(find_one)
    s._tenant_cache["t1"] = Tenant(**_doc())

    await s._handle_update(_remove())

    assert "t1" not in s._tenant_cache
    find_one.assert_not_awaited()


async def test_database_failure_during_reread_keeps_cached_entry(caplog):
    s = _svc(AsyncMock(side_effect=Exception("mongodb://secret-host")))
    cached = Tenant(**_doc())
    s._tenant_cache["t1"] = cached
    caplog.set_level(logging.INFO, logger=TENANT_LOGGER)

    await s._handle_update(_upsert(DbConnectionString="mongodb://evil"))

    assert s._tenant_cache["t1"] is cached
    warnings = [
        r for r in caplog.records
        if r.name == TENANT_LOGGER and r.levelno == logging.WARNING
    ]
    assert len(warnings) == 1
    assert "t1" in warnings[0].getMessage()
    assert "Exception" in warnings[0].getMessage()
    assert "secret-host" not in caplog.text

    await s._handle_update(_upsert("t9"))
    assert "t9" not in s._tenant_cache


async def test_published_upsert_still_carries_whole_tenant():
    s = _svc()
    s.cache.publish_async = AsyncMock()

    await s.update_tenant_version_async(TenantCacheUpdateMessage(action="upsert", tenant=_t("t1")))

    s.cache.publish_async.assert_awaited_once()
    channel, payload = s.cache.publish_async.await_args.args
    assert channel == "tenant::updates"
    body = json.loads(payload)
    assert body["Action"] == "upsert"
    assert body["TenantId"] == "t1"
    assert body["Tenant"]["TenantId"] == "t1"
    assert body["Tenant"]["DbConnectionString"] == "conn"


# ---------------------------------------------------------------------------
# Certificates
# ---------------------------------------------------------------------------

def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _cert_pem(key, lifetime=datetime.timedelta(days=365)):
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "tenant.example")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(_now() - datetime.timedelta(days=1))
        .not_valid_after(_now() + lifetime)
        .sign(key, hashes.SHA256())
    )
    return certificate.public_bytes(serialization.Encoding.PEM)


@pytest.fixture(scope="module")
def keys():
    real, attacker = _key(), _key()
    return {
        "real": real,
        "attacker": attacker,
        "real_pem": _cert_pem(real),
        "attacker_pem": _cert_pem(attacker),
    }


class _Clock:
    def __init__(self, start=1000.0):
        self.now = start

    def __call__(self):
        return self.now


def _sign(key, issuer, audience):
    now = _now()
    claims = {
        "sub": "user-1",
        "iss": issuer,
        "aud": audience,
        "iat": now,
        "nbf": now,
        "exp": now + datetime.timedelta(minutes=30),
    }
    private_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return jwt.encode(claims, private_pem, algorithm="RS256", headers={"kid": "k1"})


def _primary_tenant(path=REAL_CERT_PATH):
    return Tenant(
        _id="ABC001",
        TenantId="ABC001",
        JwtTokenParameters={"PublicCertificatePath": path, "Issuer": "blocks", "Audiences": ["a"]},
    )


def _request():
    request = MagicMock()
    request.url = URL("https://api.klax.io/x")
    request.headers = {}
    request.query_params = {}
    return request


def _planted_redis(certificate):
    cache = MagicMock()
    cache.get_bytes_value.return_value = certificate
    cache.add_bytes_value_async = AsyncMock()
    return cache


def _provider():
    return ThirdPartyJwtProvider(
        _id="p1", TenantId="ABC001", Key="auth0-main", IsActive=True,
        Issuer=AUTH0, Audiences=[AUDIENCE], Algorithms=[1],
        ClaimsMapping={"UserId": "sub"}, PublicCertificatePath=PROVIDER_CERT_PATH,
    )


async def _validate_third_party(token, cache):
    store = MagicMock()
    store.get_active = AsyncMock(return_value=[_provider()])
    tenant = Tenant(_id="ABC001", TenantId="ABC001", IsThirdPartyJwtEnabled=True)
    with patch.object(auth, "get_third_party_provider_store", return_value=store):
        return await auth.validate_with_fallback(token, tenant, _request(), cache)


async def test_planted_primary_certificate_in_redis_is_ignored(keys):
    cache = _planted_redis(keys["attacker_pem"])
    tenant = _primary_tenant()

    with patch.object(auth, "fetch_cert_bytes", new_callable=AsyncMock, return_value=keys["real_pem"]) as fetch:
        assert await auth.get_tenant_cert(cache, tenant, "ABC001") == keys["real_pem"]
        fetch.assert_awaited()
        assert fetch.await_args.args[0] == REAL_CERT_PATH

        with pytest.raises(InvalidTokenError):
            await auth.validate_jwt_token(_sign(keys["attacker"], "blocks", "a"), tenant, cache, _request())
        payload = await auth.validate_jwt_token(_sign(keys["real"], "blocks", "a"), tenant, cache, _request())

    assert payload["sub"] == "user-1"
    cache.get_bytes_value.assert_not_called()
    cache.add_bytes_value_async.assert_not_awaited()


async def test_planted_provider_certificate_in_redis_is_ignored(keys):
    cache = _planted_redis(keys["attacker_pem"])

    with patch.object(auth, "fetch_cert_bytes", new_callable=AsyncMock, return_value=keys["real_pem"]):
        assert await _validate_third_party(_sign(keys["attacker"], AUTH0, AUDIENCE), cache) is None
        assert await _validate_third_party(_sign(keys["real"], AUTH0, AUDIENCE), cache) is not None

    cache.get_bytes_value.assert_not_called()
    cache.add_bytes_value_async.assert_not_awaited()


async def test_certificates_cached_in_process_with_ttl(keys, monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(auth, "_certificate_cache", auth._CertificateCache(clock))
    cache = MagicMock()
    tenant = _primary_tenant()
    primary_token = _sign(keys["real"], "blocks", "a")
    provider_token = _sign(keys["real"], AUTH0, AUDIENCE)

    with patch.object(auth, "fetch_cert_bytes", new_callable=AsyncMock, return_value=keys["real_pem"]) as fetch:
        await auth.validate_jwt_token(primary_token, tenant, cache, _request())
        await auth.validate_jwt_token(primary_token, tenant, cache, _request())
        assert fetch.await_count == 1
        clock.now += 3600
        await auth.validate_jwt_token(primary_token, tenant, cache, _request())
        assert fetch.await_count == 2

    with patch.object(auth, "fetch_cert_bytes", new_callable=AsyncMock, return_value=keys["real_pem"]) as fetch:
        assert await _validate_third_party(provider_token, cache) is not None
        assert await _validate_third_party(provider_token, cache) is not None
        assert fetch.await_count == 1
        clock.now += 3600
        assert await _validate_third_party(provider_token, cache) is not None
        assert fetch.await_count == 2

    short = _cert_pem(keys["real"], lifetime=datetime.timedelta(seconds=120))
    short_tenant = _primary_tenant("https://certs.example/short.cer")
    with patch.object(auth, "fetch_cert_bytes", new_callable=AsyncMock, return_value=short) as fetch:
        await auth.get_tenant_cert(cache, short_tenant, "ABC001")
        await auth.get_tenant_cert(cache, short_tenant, "ABC001")
        assert fetch.await_count == 1
        clock.now += 121
        await auth.get_tenant_cert(cache, short_tenant, "ABC001")
        assert fetch.await_count == 2
