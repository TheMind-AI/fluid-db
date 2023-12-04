import abc


class FunctionBase(object):
    
    def __call__(self):
        pass
    
    @abc.abstractmethod
    def run():
        raise NotImplementedError()