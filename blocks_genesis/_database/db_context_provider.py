from abc import ABC, abstractmethod
from pymongo.collection import Collection
from pymongo.database import Database
from typing import Optional


class DbContextProvider(ABC):
    @abstractmethod
    async def get_database(self, tenant_id: Optional[str] = None) -> Optional[Database]:
        pass

    @abstractmethod
    async def get_collection(self, collection_name: str, tenant_id: Optional[str] = None) -> Collection:
        pass
