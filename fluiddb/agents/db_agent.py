from abc import ABC, abstractmethod


class DBAgent(ABC):
    
    @abstractmethod
    def save(self, text: str):
        pass

    @abstractmethod
    def maybe_save(self, text: str):
        pass

    @abstractmethod
    def fetch(self, query: str, metadata: dict = {}):
        pass

