import pytest
from unittest.mock import MagicMock
from blocks_genesis._database.db_context import DbContext

def test_set_and_get_provider():
    provider = MagicMock()
    DbContext.set_provider(provider)
    assert DbContext.get_provider() is provider

def test_clear_provider():
    provider = MagicMock()
    DbContext.set_provider(provider)
    DbContext.clear()
    with pytest.raises(RuntimeError):
        DbContext.get_provider() 