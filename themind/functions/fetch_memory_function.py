from typing import Type
from pydantic import BaseModel, Field
from themind.functions.function_base import FunctionBase
from themind.retrievers.dummy_retriever import DummyRetriever
from themind.retrievers.retriever_base import RetrieverBase


class FetchMemoryFunctionArguments(BaseModel):
    query: str = Field(..., description="Query to fetch memory")


class FetchMemoryFunction(FunctionBase):
    
    name: str = "retrieve-memory"
    description: str = "Tool to retrieve memories from the user's structured memory. It uses JSONPath to query the memory; write the query based on the provided JSON schema."
    args_schema: Type[BaseModel] = FetchMemoryFunctionArguments
    
    def __init__(self, retriever: RetrieverBase = DummyRetriever()):
        super().__init__()
        self.retriever = retriever
        
    def run(self, uid: str, query: str):
        return self.retriever.retrieve(uid=uid, query=query)


