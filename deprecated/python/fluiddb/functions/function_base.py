import abc
from pydantic import BaseModel
from typing import List, Type


class FunctionBase(object):
    
    name: str
    description: str
    args_schema: Type[BaseModel]
    
    @abc.abstractmethod
    def run(self, uid: str, messages: List[str]):
        raise NotImplementedError()

    def openai_schema(self):
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.args_schema.model_json_schema()
            }
        }
