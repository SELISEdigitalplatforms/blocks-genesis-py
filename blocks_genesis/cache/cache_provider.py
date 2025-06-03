class CacheProvider:
    """
    Singleton class for managing cache client implementation
    """
    _instance = None
    _cache_client = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(CacheProvider, cls).__new__(cls)
        return cls._instance

    @staticmethod
    def get_cache_client():
        """
        Get the current cache client implementation
        Returns:
            The configured cache client
        Raises:
            RuntimeError: If cache client is not initialized
        """
        if CacheProvider._cache_client is None:
            raise RuntimeError("Cache client not initialized")
        return CacheProvider._cache_client

