import pytest
import os
import tempfile
import json
from blocks_genesis._core import configuration

def test_load_and_get_configurations(tmp_path, monkeypatch):
    config = {'foo': 'bar'}
    config_path = tmp_path / 'dev.json'
    with open(config_path, 'w') as f:
        json.dump(config, f)
    configuration.load_configurations(str(tmp_path))
    assert configuration.get_configurations()['foo'] == 'bar'

def test_load_configurations_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        configuration.load_configurations(str(tmp_path))

def test_get_configurations_not_loaded():
    # Reset the module-level variable
    configuration._app_configurations = None
    with pytest.raises(Exception):
        configuration.get_configurations() 