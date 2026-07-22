import pytest
from unittest.mock import AsyncMock
from blocks_genesis._core.azure_key_vault import AzureKeyVault
from blocks_genesis._core.secret_loader import SecretLoader


@pytest.mark.asyncio
async def test_extract_vault_url_missing_raises():
    v = AzureKeyVault()
    with pytest.raises(ValueError):
        v._extract_vault_url({})


@pytest.mark.asyncio
async def test_get_secrets_from_vault_skips_empty_value():
    v = AzureKeyVault()
    v._get_secret = AsyncMock(side_effect=lambda k: '' if k == 'B' else 'v')
    result = await v._get_secrets_from_vault(['A', 'B'])
    assert result == {'A': 'v'}


@pytest.mark.asyncio
async def test_close_with_none_credential_and_client():
    v = AzureKeyVault()
    v.credential = None
    v.secret_client = None
    await v.close()


@pytest.mark.asyncio
async def test_secret_loader_close_when_vault_has_no_close():
    loader = SecretLoader('svc')
    loader.vault = object()
    await loader.close()
