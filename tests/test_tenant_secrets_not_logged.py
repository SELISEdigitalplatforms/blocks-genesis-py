"""Tenant secrets never reach the logs.

A tenant update used to be logged in full, which wrote the tenant's database connection
string, salt and certificate passwords at INFO on every service. These tests feed sentinel
secrets through the update handler, the database load path and the models themselves.
"""
import asyncio
import json
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from blocks_genesis._tenant.tenant import Tenant
from blocks_genesis._tenant.tenant_service import TenantService

SECRETS = {
    "DbConnectionString": "mongodb://admin:SENTINEL-DB-PASSWORD@db.internal:27017",
    "TenantSalt": "SENTINEL-TENANT-SALT",
    "PublicCertificatePassword": "SENTINEL-PUBLIC-CERT-PASSWORD",
    "PrivateCertificatePassword": "SENTINEL-PRIVATE-CERT-PASSWORD",
    "ThirdPartyCertificatePassword": "SENTINEL-THIRD-PARTY-CERT-PASSWORD",
}


def _svc():
    s = TenantService.__new__(TenantService)
    s.cache = MagicMock()
    s.database = MagicMock()
    s._tenant_cache = {}
    s._tenant_load_in_progress = {}
    s._tenant_loaded_at = {}
    s._unknown_tenants = {}
    s._unknown_domains = {}
    s._update_channel = "tenant::updates"
    s._collection_name = "Tenants"
    s._initialized = False
    s._is_subscribed = False
    s._disposed = False
    s._initialize_lock = asyncio.Lock()
    s._tenant_load_lock = asyncio.Lock()
    return s


def _doc():
    return {
        "_id": "t1",
        "TenantId": "t1",
        "DBName": "db",
        "DbConnectionString": SECRETS["DbConnectionString"],
        "TenantSalt": SECRETS["TenantSalt"],
        "JwtTokenParameters": {
            "Issuer": "iss",
            "PublicCertificatePassword": SECRETS["PublicCertificatePassword"],
            "PrivateCertificatePassword": SECRETS["PrivateCertificatePassword"],
        },
        "ThirdPartyJwtTokenParameters": {
            "PublicCertificatePassword": SECRETS["ThirdPartyCertificatePassword"],
        },
    }


def _assert_no_secret(text):
    # pydantic truncates long inputs to their head and tail, so check a tail fragment too.
    for name, value in SECRETS.items():
        assert value not in text, f"{name} leaked"
        assert value[-10:] not in text, f"{name} leaked in part"
    assert "SENTINEL" not in text


async def test_a_tenant_update_logs_no_secret_and_still_applies(caplog):
    svc = _svc()
    # The update re-reads the tenant from the root database; the message body is ignored.
    collection = MagicMock()
    collection.find_one = AsyncMock(return_value=_doc())
    svc.database = {"Tenants": collection}
    caplog.set_level(logging.DEBUG)

    await svc._handle_update(json.dumps({"Action": "upsert", "Tenant": _doc()}))

    _assert_no_secret("\n".join(r.getMessage() for r in caplog.records))
    assert svc._tenant_cache["t1"].db_connection_string == SECRETS["DbConnectionString"]


def test_the_tenant_repr_shows_no_secret():
    tenant = Tenant(**_doc())

    _assert_no_secret(repr(tenant))
    _assert_no_secret(str(tenant))


@pytest.mark.parametrize("field", ["DbConnectionString", "TenantSalt"])
def test_a_validation_error_does_not_echo_the_input(field):
    doc = _doc()
    doc[field] = {"wrapped": SECRETS[field]}

    with pytest.raises(ValidationError) as err:
        Tenant(**doc)

    _assert_no_secret(str(err.value))


async def test_a_bad_tenant_document_from_the_database_logs_no_secret(caplog):
    svc = _svc()
    doc = _doc()
    doc["TenantSalt"] = [SECRETS["TenantSalt"]]
    collection = MagicMock()
    collection.find_one = AsyncMock(return_value=doc)
    svc.database = {"Tenants": collection}
    caplog.set_level(logging.DEBUG)

    await svc._handle_update(json.dumps({"Action": "upsert", "TenantId": "t1"}))
    assert "t1" not in svc._tenant_cache

    logged = "\n".join(r.getMessage() + (r.exc_text or "") for r in caplog.records)
    for record in caplog.records:
        if record.exc_info:
            logged += logging.Formatter().formatException(record.exc_info)
    _assert_no_secret(logged)
