from typing import Optional

from blocks_genesis._database.mongo_context import MongoDbContextProvider


class DbContext:
    _provider: Optional[MongoDbContextProvider] = None

    @classmethod
    def set_provider(cls, provider: MongoDbContextProvider) -> None:
        cls._provider = provider

    @classmethod
    def get_provider(cls) -> MongoDbContextProvider:
        if cls._provider is None:
            raise RuntimeError("No MongoDbContextProvider registered.")
        return cls._provider

    @classmethod
    def clear(cls) -> None:
        cls._provider = None
