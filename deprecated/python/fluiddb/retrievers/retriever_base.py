import abc


class RetrieverBase(object):
    
    def __init__(self):
        pass
    
    @abc.abstractmethod
    def retrieve(self, uid: str, query: str):
        raise NotImplementedError("Subclasses should implement this!")



