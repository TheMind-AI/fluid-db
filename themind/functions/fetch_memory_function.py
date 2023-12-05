from typing import Type
from pydantic import BaseModel, Field
from themind.schema.thread import Thread
from themind.functions.function_base import FunctionBase
from themind.retrivers.retriver_base import RetriverBase
from themind.retrivers.dummy_retriver import DummyRetriver


class FetchMemoryFunctionArguments(BaseModel):
    """Arguments for the E2BDataAnalysisTool."""

    user_text_message: str = Field(
        ...,
        example="What sports do I like?",
        description=(
            "User message to be used to fetch from structured memory."
        ),
    )


class FetchMemoryFunction(FunctionBase):
    
    name: str = "Retrive Memory"
    description: str = "Tool to retrive memory from the user"
    args_schema: Type[BaseModel] = FetchMemoryFunctionArguments
    
    def __init__(self, retriver: RetriverBase = DummyRetriver()):
        super().__init__()
        self.retriver = retriver
        
    def run(self, uid: str, query: str, thread: Thread):
        # fetch memory from the user
        thread.to_context_str()
        # initialize retriver
        memory_retriver = Retriver(uid=uid)
        memory_retriver.retrive(query='', context=thread.to_context_str())
        
        
        # return the memory
        # stream message to the user
