import abc

class RetrieverBase:
    
    def __init__(self):
        pass
    
    @abc.abstractmethod
    def retrieve(self, uid: str, query: str, context: str):
        raise NotImplementedError("Subclasses should implement this!")