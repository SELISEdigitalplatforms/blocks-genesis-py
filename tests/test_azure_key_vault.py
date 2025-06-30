import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from blocks_genesis._core.azure_key_vault import AzureKeyVault

@patch('blocks_genesis._core.azure_key_vault.EnvVaultConfig')
@patch('blocks_genesis._core.azure_key_vault.ClientSecretCredential')
@patch('blocks_genesis._core.azure_key_vault.SecretClient')
def test_azure_key_vault_init(mock_secret_client, mock_cred, mock_env):
    mock_env.get_config.return_value = {
        'KEYVAULT__CLIENTID': 'cid',
        'KEYVAULT__CLIENTSECRET': 'csec',
        'KEYVAULT__KEYVAULTURL': 'url',
        'KEYVAULT__TENANTID': 'tid',
    }
    vault = AzureKeyVault()
    assert vault.vault_url == 'url'
    assert mock_cred.called
    assert mock_secret_client.called

@pytest.mark.asyncio
@patch('blocks_genesis._core.azure_key_vault.AzureKeyVault.get_secret_value', new_callable=AsyncMock)
async def test_get_secrets(mock_get_secret_value):
    mock_get_secret_value.side_effect = lambda k: f'val-{k}'
    vault = AzureKeyVault.__new__(AzureKeyVault)
    vault.get_secret_value = mock_get_secret_value
    keys = ['A', 'B']
    result = await vault.get_secrets(keys)
    assert result == {'A': 'val-A', 'B': 'val-B'}

@pytest.mark.asyncio
@patch('blocks_genesis._core.azure_key_vault.SecretClient')
async def test_get_secret_value_success(mock_secret_client):
    vault = AzureKeyVault.__new__(AzureKeyVault)
    vault.secret_client = mock_secret_client.return_value
    mock_secret = MagicMock()
    mock_secret.value = 'v'
    vault.secret_client.get_secret = AsyncMock(return_value=mock_secret)
    result = await vault.get_secret_value('foo')
    assert result == 'v'

@pytest.mark.asyncio
@patch('blocks_genesis._core.azure_key_vault.SecretClient')
async def test_get_secret_value_error(mock_secret_client):
    vault = AzureKeyVault.__new__(AzureKeyVault)
    vault.secret_client = mock_secret_client.return_value
    vault.secret_client.get_secret = AsyncMock(side_effect=Exception('fail'))
    result = await vault.get_secret_value('foo')
    assert result == ''

@pytest.mark.asyncio
@patch('blocks_genesis._core.azure_key_vault.ClientSecretCredential')
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