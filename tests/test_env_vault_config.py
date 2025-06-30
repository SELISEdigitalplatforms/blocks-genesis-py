import os
import pytest
from blocks_genesis._core.env_vault_config import EnvVaultConfig

def test_get_config_all(monkeypatch):
    monkeypatch.setenv('FOO', 'BAR')
    config = EnvVaultConfig.get_config()
    assert 'FOO' in config
    assert config['FOO'] == 'BAR'

def test_get_config_keys(monkeypatch):
    monkeypatch.setenv('A', '1')
    monkeypatch.setenv('B', '2')
    config = EnvVaultConfig.get_config(['A', 'B'])
    assert config['A'] == '1'
    assert config['B'] == '2'

def test_get_config_missing(monkeypatch):
    monkeypatch.delenv('X', raising=False)
    with pytest.raises(EnvironmentError):
        EnvVaultConfig.get_config(['X']) 