import pytest
from blocks_genesis._cache.cache_provider import CacheProvider

class DummyCache:
    pass

def test_set_and_get_client():
    dummy = DummyCache()
    CacheProvider.set_client(dummy)
    assert CacheProvider.get_client() is dummy

def test_clear_client():
    dummy = DummyCache()
    CacheProvider.set_client(dummy)
    CacheProvider.clear()
    with pytest.raises(RuntimeError):
        CacheProvider.get_client() 