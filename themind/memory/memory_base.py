import abc


class MemoryBase(object):
    id: str

    @abc.abstractmethod
    def query(self, query: str) -> list:
        raise NotImplementedError()
    
    @abc.abstractmethod
    def schema(self) -> str:
        raise NotImplementedError()
    
    @abc.abstractmethod
    def update(self, path: str, new_data: dict):
        raise NotImplementedError()
    
    def query_lang_prompt(self) -> str:
        raise NotImplementedError()
