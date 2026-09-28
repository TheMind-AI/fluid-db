from abc import ABC, abstractmethod


class DatabaseEngine(ABC):
    
    @property
    @abstractmethod
    def engine_name(self) -> str:
        pass
    
    @abstractmethod
    def connect(self, db_id: str):
        pass

    @abstractmethod
    def query(self, uid: str, query: str):
        pass

    @abstractmethod
    def schema(self, uid: str) -> str:
        pass
