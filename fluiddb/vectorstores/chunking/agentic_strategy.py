import csv
from typing import List
from pydantic import BaseModel
from langchain_core.documents import Document
from themind.llm.func_instraction import instruct
from themind.vectorstores.chunking.chunking_strategy import ChunkingStrategy


class QAModel(BaseModel):
    reasoning: List[str]
    question: List[str]
    answers: List[str]


class AgenticStrategy(ChunkingStrategy):

    def chunk(self, data: str) -> List[Document]:
        
        


