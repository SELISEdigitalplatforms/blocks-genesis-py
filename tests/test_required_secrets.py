import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from blocks_genesis._cache.redis_client import RedisClient
from blocks_genesis._core import secret_loader
from blocks_genesis._core.azure_key_vault import AzureKeyVault

SL = 'blocks_genesis._core.secret_loader.'
RC = 'blocks_genesis._cache.redis_client.'

ALL_REQUIRED = {
    'CacheConnectionString': 'cache-host:6379,password=cache-pass',
    'DatabaseConnectionString': 'mongodb://db-user:db-pass@db-host',
    'RootDatabaseName': 'root-db',
}


@pytest.fixture(autouse=True)
def no_loaded_secret():
    secret_loader._loaded_secret = None
    yield
    secret_loader._loaded_secret = None


async def _load(vault_values, fields=None):
    with patch(SL + 'AzureKeyVault') as vault:
        vault.return_value.get_secrets = AsyncMock(return_value=vault_values)
        await secret_loader.SecretLoader('svc').load_secrets(fields)


@pytest.mark.parametrize('missing', sorted(ALL_REQUIRED))
async def test_missing_required_secret_refuses_to_load(missing):
    values = {k: v for k, v in ALL_REQUIRED.items() if k != missing}
    with pytest.raises(RuntimeError) as err:
        await _load(values)
    message = str(err.value)
    assert missing in message
    for value in ALL_REQUIRED.values():
        assert value not in message
    assert secret_loader._loaded_secret is None


async def test_blank_required_secret_refuses_to_load():
    with pytest.raises(RuntimeError, match='RootDatabaseName'):
        await _load({**ALL_REQUIRED, 'RootDatabaseName': '   '})
    assert secret_loader._loaded_secret is None


async def test_all_required_secrets_present_loads():
    await _load(dict(ALL_REQUIRED))
    secret = secret_loader.get_blocks_secret()
    assert secret.CacheConnectionString == ALL_REQUIRED['CacheConnectionString']
    assert secret.ServiceName == 'svc'


async def test_required_secret_not_requested_is_not_enforced():
    await _load({'MessageConnectionString': 'amqp://broker'}, fields=['MessageConnectionString'])
    assert secret_loader.get_blocks_secret().MessageConnectionString == 'amqp://broker'


async def test_vault_error_logs_key_and_type_without_message(caplog):
    vault = AzureKeyVault()
    vault.secret_client = MagicMock()
    vault.secret_client.get_secret = AsyncMock(side_effect=ValueError('leaked-detail-xyz'))
    with caplog.at_level(logging.WARNING):
        assert await vault._get_secret('CacheConnectionString') == ''
    assert 'CacheConnectionString' in caplog.text
    assert 'ValueError' in caplog.text
    assert 'leaked-detail-xyz' not in caplog.text


@pytest.mark.parametrize('connection_string', ['', '   '])
@patch(RC + 'redis')
@patch(RC + 'get_blocks_secret')
def test_redis_client_refuses_empty_connection_string(mock_gs, mock_redis, connection_string):
    mock_gs.return_value.CacheConnectionString = connection_string
    with pytest.raises(ValueError, match='CacheConnectionString is not configured'):
        RedisClient()
    mock_redis.Redis.assert_not_called()
