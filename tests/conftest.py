import datetime
import os

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from blocks_genesis._delegation.constants import IAM_TOKEN_ENDPOINT_KEY
from blocks_genesis._delegation.endpoint_resolver import set_endpoint_resolver


@pytest.fixture(autouse=True, scope="session")
def delegated_access_configured():
    """Give the suite the minimum delegated-access configuration.

    Startup refuses to run when neither `BLOCKS_IAM_BASE_URL` nor `BLOCKS_IAM_TOKEN_ENDPOINT` is
    set -- the token endpoint is never guessed -- so the startup tests need one of them present.
    Tests that assert the unconfigured behaviour clear these with `monkeypatch.delenv`.
    """
    previous = os.environ.get(IAM_TOKEN_ENDPOINT_KEY)
    os.environ[IAM_TOKEN_ENDPOINT_KEY] = "http://blocks-iam:8080/api/oidc/token"

    yield

    set_endpoint_resolver(None)
    if previous is None:
        os.environ.pop(IAM_TOKEN_ENDPOINT_KEY, None)
    else:
        os.environ[IAM_TOKEN_ENDPOINT_KEY] = previous


@pytest.fixture(autouse=True)
def empty_certificate_cache():
    """Certificates are cached in process, so one test's certificate must not serve another."""
    from blocks_genesis._auth import auth

    auth._certificate_cache.clear()
    yield
    auth._certificate_cache.clear()


@pytest.fixture(autouse=True)
def forget_provider_warnings():
    """Provider configuration warnings are logged once per process, so one test's warning
    must not silence another's."""
    from blocks_genesis._auth import auth

    auth._provider_warnings_logged.clear()
    yield
    auth._provider_warnings_logged.clear()


@pytest.fixture(autouse=True)
def available_message_tenant(monkeypatch):
    """Workers look up the message's tenant; by default it exists and is enabled."""
    from unittest.mock import AsyncMock, MagicMock

    from blocks_genesis._message import tenant_check
    from blocks_genesis._tenant.tenant import Tenant

    tenant = Tenant(_id="tenant-1", TenantId="tenant-1")
    service = MagicMock(resolve_tenant=AsyncMock(return_value=tenant))
    monkeypatch.setattr(tenant_check, "get_tenant_service", lambda: service)


@pytest.fixture(scope="session")
def valid_cert_pem():
    """A self-signed PEM certificate valid from yesterday for a year."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "tenant.example")])
    now = datetime.datetime.now(datetime.timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=365))
        .sign(key, hashes.SHA256())
    )
    return certificate.public_bytes(serialization.Encoding.PEM)
