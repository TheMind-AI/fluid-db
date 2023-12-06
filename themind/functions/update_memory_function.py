from typing import Type
from pydantic import BaseModel, Field
from themind.functions.function_base import FunctionBase


class UpdateMemoryFunction(FunctionBase):

    name: str = "update-core-memory"
    description: str = "Tool to update the structured memory of the user."
    args_schema: Type[BaseModel] = None

    def __init__(self):
        super().__init__()
        
    def run(self, uid: str, message: str):
        raise NotImplementedError()
    
        # run internal dialog how to update the core memory
        # update scheme & the json
