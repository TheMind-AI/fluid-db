from typing import List
from abc import ABC, abstractmethod
from langchain_core.documents import Document


class ChunkingStrategy(ABC):

    @abstractmethod
    def chunk(self, data: str) -> List[Document]:
        pass