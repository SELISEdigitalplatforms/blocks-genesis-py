import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from blocks_genesis._core import secret_loader

@pytest.mark.asyncio
@patch('blocks_genesis._core.secret_loader.AzureKeyVault')
@patch('blocks_genesis._core.secret_loader.BlocksSecret')
async def test_load_secrets_and_get_blocks_secret(mock_blocks_secret, mock_vault, monkeypatch):
    # Reset module-level _loaded_secret
    secret_loader._loaded_secret = None
    mock_vault_instance = mock_vault.return_value
    mock_vault_instance.get_secrets = AsyncMock(return_value={'CacheConnectionString': 'foo', 'ServiceName': 'svc'})
    mock_blocks_secret.return_value = MagicMock()
    loader = secret_loader.SecretLoader('svc')
    await loader.load_secrets()
    # Should set _loaded_secret
    assert secret_loader._loaded_secret is not None
    # get_blocks_secret returns the loaded secret
    assert secret_loader.get_blocks_secret() is not None

@pytest.mark.asyncio
@patch('blocks_genesis._core.secret_loader.AzureKeyVault')
@patch('blocks_genesis._core.secret_loader.BlocksSecret')
async def test_load_secrets_already_loaded(mock_blocks_secret, mock_vault):
    secret_loader._loaded_secret = MagicMock()
    loader = secret_loader.SecretLoader('svc')
    await loader.load_secrets()
    # Should not reload
    mock_vault.return_value.get_secrets.assert_not_called()

@pytest.mark.asyncio
@patch('blocks_genesis._core.secret_loader.AzureKeyVault')
@patch('blocks_genesis._core.secret_loader.BlocksSecret')
async def test_load_secrets_error(mock_blocks_secret, mock_vault):
    secret_loader._loaded_secret = None
    mock_vault_instance = mock_vault.return_value
    mock_vault_instance.get_secrets = AsyncMock(side_effect=Exception('fail'))
    loader = secret_loader.SecretLoader('svc')
    with pytest.raises(Exception):
        await loader.load_secrets()

@pytest.mark.asyncio
@patch('blocks_genesis._core.secret_loader.AzureKeyVault')
async def test_close(mock_vault):
    loader = secret_loader.SecretLoader('svc')
    mock_vault_instance = mock_vault.return_value
    mock_vault_instance.close = AsyncMock()
    await loader.close()
    mock_vault_instance.close.assert_awaited()

def test_get_blocks_secret_not_loaded():
    secret_loader._loaded_secret = None
    with pytest.raises(Exception):
        secret_loader.get_blocks_secret() 