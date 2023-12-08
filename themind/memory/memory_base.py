import abc


class MemoryBase(object):
    id: str

    @abc.abstractmethod
    def query(self, uid: str, query: str) -> list:
        raise NotImplementedError()
    
    @abc.abstractmethod
    def schema(self, uid: str) -> str:
        raise NotImplementedError()

    @abc.abstractmethod
    def update(self, uid: str, path: str, new_data: dict):
        raise NotImplementedError()

    @abc.abstractmethod
    def query_lang_prompt(self, uid: str) -> str:
        raise NotImplementedError()
