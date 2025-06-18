from abc import ABC, abstractmethod
from pymongo.collection import Collection
from typing import Optional


class DbContextProvider(ABC):
    @abstractmethod
    def get_database(self, tenant_id: Optional[str] = None):
        pass

    @abstractmethod
    def get_collection(self, collection_name: str, tenant_id: Optional[str] = None) -> Collection:
        pass
