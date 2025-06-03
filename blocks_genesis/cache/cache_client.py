from abc import ABC, abstractmethod
from typing import Dict, Optional, Callable, Any


class ICacheClient(ABC):
    """Abstract base class for cache client operations"""
    
    @abstractmethod
    def key_exists(self, key: str) -> bool:
        """Check if key exists in cache"""
        pass
    
    @abstractmethod
    def add_string_value(self, key: str, value: str, key_life_span: Optional[int] = None) -> bool:
        """Add string value to cache"""
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
        """Add hash value to cache"""
        pass
    
    @abstractmethod
    def get_hash_value(self, key: str) -> Dict[str, Any]:
        """Get hash value from cache"""
        pass
    
    # Async methods
    @abstractmethod
    async def key_exists_async(self, key: str) -> bool:
        """Check if key exists in cache (async)"""
        pass
    
    @abstractmethod
    async def add_string_value_async(self, key: str, value: str, key_life_span: Optional[int] = None) -> bool:
        """Add string value to cache (async)"""
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
        """Add hash value to cache (async)"""
        pass
    
    @abstractmethod
    async def get_hash_value_async(self, key: str) -> Dict[str, Any]:
        """Get hash value from cache (async)"""
        pass
    
    # Pub/Sub methods
    @abstractmethod
    async def publish_async(self, channel: str, message: str) -> int:
        """Publish message to channel"""
        pass
    
    @abstractmethod
    async def subscribe_async(self, channel: str, handler: Callable[[str, str], None]) -> None:
        """Subscribe to channel with handler"""
        pass
    
    @abstractmethod
    async def unsubscribe_async(self, channel: str) -> None:
        """Unsubscribe from channel"""
        pass