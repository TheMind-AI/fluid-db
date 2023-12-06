import abc


class MemoryBase(object):
    id: str

    @abc.abstractmethod
    def query(query: str) -> list:
        raise NotImplementedError()
    
    @abc.abstractmethod
    def schema() -> str:
        raise NotImplementedError()
    
    @abc.abstractmethod
    def update(path: str, new_data: dict):
        raise NotImplementedError()
    
    def query_lang_prompt() -> str:
        raise NotImplementedError()
