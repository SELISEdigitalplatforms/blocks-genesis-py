from blocks_genesis.database.db_context_provider import DbContextProvider


class DbContext:
    """
    Singleton class for managing a global DbContextProvider.
    """

    _provider: DbContextProvider = None

    @staticmethod
    def set_provider(provider: DbContextProvider) -> None:
        """
        Set the global DB context provider.

        Args:
            provider: An instance of DbContextProvider to use globally.
        """
        DbContext._provider = provider

    @staticmethod
    def get_provider() -> DbContextProvider:
        """
        Get the global DB context provider.

        Returns:
            The configured DbContextProvider.

        Raises:
            RuntimeError: If no provider has been set.
        """
        if DbContext._provider is None:
            raise RuntimeError("No DbContextProvider registered.")
        return DbContext._provider

    @staticmethod
    def clear() -> None:
        """
        Clear the global DB context provider.
        """
        DbContext._provider = None
