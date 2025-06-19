from typing import Optional
from blocks_genesis._database.db_context_provider import DbContextProvider


class DbContext:
    _provider: Optional[DbContextProvider] = None

    @classmethod
    def set_provider(cls, provider: DbContextProvider) -> None:
        cls._provider = provider

    @classmethod
    def get_provider(cls) -> DbContextProvider:
        if cls._provider is None:
            raise RuntimeError("No DbContextProvider registered.")
        return cls._provider

    @classmethod
    def clear(cls) -> None:
        cls._provider = None
