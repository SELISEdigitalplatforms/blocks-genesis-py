import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from blocks_genesis._core.azure_key_vault import AzureKeyVault

def test_azure_key_vault_init():
    vault = AzureKeyVault()
    assert vault.vault_url is None
    assert vault.credential is None
    assert vault.secret_client is None

@pytest.mark.asyncio
@patch('blocks_genesis._core.azure_key_vault.SecretClient')
@patch('blocks_genesis._core.azure_key_vault.DefaultAzureCredential')
@patch('blocks_genesis._core.azure_key_vault.EnvVaultConfig')
async def test_get_secrets(mock_env, mock_cred, mock_secret_client):
    mock_env.get_config.return_value = {'KEYVAULT__KEYVAULTURL': 'https://vault'}
    vault = AzureKeyVault()
    vault._get_secret = AsyncMock(side_effect=lambda k: f'val-{k}')
    result = await vault.get_secrets(['A', 'B'])
    assert result == {'A': 'val-A', 'B': 'val-B'}

@pytest.mark.asyncio
@patch('blocks_genesis._core.azure_key_vault.SecretClient')
async def test__get_secret_success(mock_secret_client):
    vault = AzureKeyVault.__new__(AzureKeyVault)
    vault.secret_client = mock_secret_client.return_value
    mock_secret = MagicMock()
    mock_secret.value = 'v'
    vault.secret_client.get_secret = AsyncMock(return_value=mock_secret)
    result = await vault._get_secret('foo')
    assert result == 'v'

@pytest.mark.asyncio
@patch('blocks_genesis._core.azure_key_vault.SecretClient')
async def test__get_secret_error(mock_secret_client):
    vault = AzureKeyVault.__new__(AzureKeyVault)
    vault.secret_client = mock_secret_client.return_value
    vault.secret_client.get_secret = AsyncMock(side_effect=Exception('fail'))
    result = await vault._get_secret('foo')
    assert result == ''

@pytest.mark.asyncio
@patch('blocks_genesis._core.azure_key_vault.DefaultAzureCredential')
@patch('blocks_genesis._core.azure_key_vault.SecretClient')
async def test_close(mock_secret_client, mock_cred):
    vault = AzureKeyVault.__new__(AzureKeyVault)
    vault.credential = MagicMock()
    vault.secret_client = MagicMock()
    vault.credential.close = AsyncMock()
    vault.secret_client.close = AsyncMock()
    await vault.close()
    vault.credential.close.assert_awaited()
    vault.secret_client.close.assert_awaited() 