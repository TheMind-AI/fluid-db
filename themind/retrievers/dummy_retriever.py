import abc
from themind.memory.struct_memory import StructuredMemory
from themind.retrievers.retriever_base import RetriverBase


class DummyRetriever(RetriverBase):
    
    def __init__(self):
        super().__init__()
        
        self.struct_memory = StructuredMemory()
    
    @abc.abstractmethod
    def retrieve(self, uid: str, query: str, context: str):
        
        # fetch json reduced schema
        # ask ai to generate a jsonpath query based on the context and reduced json schema
        # run it and return the data
