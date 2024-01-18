import csv
from typing import List
from abc import ABC, abstractmethod
from langchain_core.documents import Document


class ChunkingStrategy(ABC):

    @abstractmethod
    def chunk(self, data: str) -> List[Document]:
        pass
    
    def append_to_csv(self, q, a, chunk, filename='qa_pairs.csv'):
        with open(filename, 'a', newline='') as file:
            writer = csv.writer(file)
            writer.writerow([q, a, chunk])