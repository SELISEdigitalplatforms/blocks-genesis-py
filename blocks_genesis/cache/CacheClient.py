# interfaces/cache_client.py
from abc import ABC, abstractmethod
from typing import Dict, Optional, Callable, Any
import redis


class CacheClient(ABC):
    """Abstract base class for cache client implementations"""
    
    @abstractmethod
    def cache_database(self) -> redis.Redis:
        """Get the cache database instance"""
        pass
    
    # Synchronous Methods
    @abstractmethod
    def key_exists(self, key: str) -> bool:
        """Check if key exists in cache"""
        pass
    
    @abstractmethod
    def add_string_value(self, key: str, value: str, key_life_span: Optional[int] = None) -> bool:
        """Add string value to cache with optional TTL"""
        pass
    
    @abstractmethod
    def get_string_value(self, key: str) -> Optional[str]:
        """Get string value from cache"""
        pass
    
    @abstractmethod
    def remove_key(self, key: str) -> bool:
        """Remove key from cache"""
        pass
    
    @abstractmethod
    def add_hash_value(self, key: str, value: Dict[str, Any], key_life_span: Optional[int] = None) -> bool:
        """Add hash value to cache with optional TTL"""
        pass
    
    @abstractmethod
    def get_hash_value(self, key: str) -> Dict[str, Any]:
        """Get hash value from cache"""
        pass
    
    # Asynchronous Methods
    @abstractmethod
    async def key_exists_async(self, key: str) -> bool:
        """Check if key exists in cache (async)"""
        pass
    
    @abstractmethod
    async def add_string_value_async(self, key: str, value: str, key_life_span: Optional[int] = None) -> bool:
        """Add string value to cache with optional TTL (async)"""
        pass
    
    @abstractmethod
    async def get_string_value_async(self, key: str) -> Optional[str]:
        """Get string value from cache (async)"""
        pass
    
    @abstractmethod
    async def remove_key_async(self, key: str) -> bool:
        """Remove key from cache (async)"""
        pass
    
    @abstractmethod
    async def add_hash_value_async(self, key: str, value: Dict[str, Any], key_life_span: Optional[int] = None) -> bool:
        """Add hash value to cache with optional TTL (async)"""
        pass
    
    @abstractmethod
    async def get_hash_value_async(self, key: str) -> Dict[str, Any]:
        """Get hash value from cache (async)"""
        pass
    
    # Pub/Sub Methods
    @abstractmethod
    async def publish_async(self, channel: str, message: str) -> int:
        """Publish message to channel"""
        pass
    
    @abstractmethod
    async def subscribe_async(self, channel: str, handler: Callable[[str, str], None]) -> None:
        """Subscribe to channel with message handler"""
        pass
    
    @abstractmethod
    async def unsubscribe_async(self, channel: str) -> None:
        """Unsubscribe from channel"""
        pass
    
    # Dispose Methods
    @abstractmethod
    def dispose(self) -> None:
        """Dispose resources synchronously"""
        pass
    
    @abstractmethod
    async def dispose_async(self) -> None:
        """Dispose resources asynchronously"""
        pass
    
    # Context Manager Support
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.dispose()
    
    async def __aenter__(self):
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.dispose_async()