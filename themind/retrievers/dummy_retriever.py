import abc
from themind.memory.structured_json_memory import StructuredJsonMemory
from themind.retrievers.retriever_base import RetrieverBase


class DummyRetriever(RetrieverBase):
    
    def __init__(self):
        super().__init__()
        
        self.struct_memory = StructuredJsonMemory(id='test')
    
    @abc.abstractmethod
    def retrieve(self, uid: str, query: str, context: str):
        raise NotImplementedError()
        # fetch json reduced schema
        # ask ai to generate a jsonpath query based on the context and reduced json schema
        # run it and return the data
