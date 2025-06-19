from typing import Any


class CacheProvider:
    """
    Singleton class for managing a global cache client.
    This client will be shared throughout the entire application lifecycle.
    """
    _cache_client: Any = None

    @staticmethod
    def set_client(cache_client: Any) -> None:
        """
        Set the global cache client implementation.

        Args:
            cache_client: An instance of the cache client to use.
        """
        CacheProvider._cache_client = cache_client

    @staticmethod
    def get_client() -> Any:
        """
        Get the global cache client.

        Returns:
            The configured cache client.

        Raises:
            RuntimeError: If the cache client is not initialized.
        """
        if CacheProvider._cache_client is None:
            raise RuntimeError("Cache client not initialized")
        return CacheProvider._cache_client

    @staticmethod
    def clear() -> None:
        """
        Clear the global cache client.
        """
        CacheProvider._cache_client = None
