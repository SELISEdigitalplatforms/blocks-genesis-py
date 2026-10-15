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
