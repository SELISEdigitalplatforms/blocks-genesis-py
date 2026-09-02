import os

import pytest

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
