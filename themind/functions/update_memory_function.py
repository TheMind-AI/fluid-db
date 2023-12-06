from typing import Type
from pydantic import BaseModel, Field
from themind.functions.function_base import FunctionBase


class UpdateMemoryFunctionArguments(BaseModel):
    uid: str = Field(..., description="User id")
    query: str = Field(..., description="Query to fetch memory")


class UpdateMemoryFunction(FunctionBase):

    name: str = "update-memory"
    description: str = "Tool to update the structured memory of the user."
    args_schema: Type[BaseModel] = UpdateMemoryFunctionArguments

    def __init__(self):
        super().__init__()
        
    def run(self, uid: str, message: str):
        raise NotImplementedError()
    
        # run internal dialog how to update the core memory
        # update scheme & the json
