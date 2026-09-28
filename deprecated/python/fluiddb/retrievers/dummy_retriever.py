import abc
from themind.memory.structured_json_memory import StructuredJsonMemory
from themind.retrievers.retriever_base import RetrieverBase


class DummyRetriever(RetrieverBase):
    
    def __init__(self):
        super().__init__()
        
        self.struct_memory = StructuredJsonMemory()
    
    @abc.abstractmethod
    def retrieve(self, uid: str, query: str):
        
        results = self.struct_memory.query(uid=uid, json_path=query)
        
        return str(results)

