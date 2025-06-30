import pytest
from blocks_genesis._cache.CacheClient import CacheClient

class DummyCacheClient(CacheClient):
    def cache_database(self):
        raise NotImplementedError()
    def key_exists(self, key):
        raise NotImplementedError()
    def add_string_value(self, key, value, key_life_span=None):
        raise NotImplementedError()
    def get_string_value(self, key):
        raise NotImplementedError()
    def remove_key(self, key):
        raise NotImplementedError()
    def add_hash_value(self, key, value, key_life_span=None):
        raise NotImplementedError()
    def get_hash_value(self, key):
        raise NotImplementedError()
    async def key_exists_async(self, key):
        raise NotImplementedError()
    async def add_string_value_async(self, key, value, key_life_span=None):
        raise NotImplementedError()
    async def get_string_value_async(self, key):
        raise NotImplementedError()
    async def remove_key_async(self, key):
        raise NotImplementedError()
    async def add_hash_value_async(self, key, value, key_life_span=None):
        raise NotImplementedError()
    async def get_hash_value_async(self, key):
        raise NotImplementedError()
    async def publish_async(self, channel, message):
        raise NotImplementedError()
    async def subscribe_async(self, channel, handler):
        raise NotImplementedError()
    async def unsubscribe_async(self, channel):
        raise NotImplementedError()
    def dispose(self):
        raise NotImplementedError()
    async def dispose_async(self):
        raise NotImplementedError()

def test_context_manager():
    client = DummyCacheClient()
    with pytest.raises(NotImplementedError):
        with client:
            pass

import asyncio
@pytest.mark.asyncio
async def test_async_context_manager():
    client = DummyCacheClient()
    with pytest.raises(NotImplementedError):
        async with client:
            pass 