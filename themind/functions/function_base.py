import abc


class FunctionBase(object):
    
    name: str
    description: str
    args_schema: Type[BaseModel]
    
    @abc.abstractmethod
    def run():
        raise NotImplementedError()