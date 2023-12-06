from typing import Type
from pydantic.fields import Field
from pydantic import BaseModel
from themind.llm.openai_llm import OpenAILLM
from themind.functions.function_base import FunctionBase


class SendMessageFunctionArguments(BaseModel):
    pass


class SendMessageFunction(FunctionBase):

    name: str = "send-message-to-user"
    description: str = "This function sends a message to the user."
    args_schema: Type[BaseModel] = SendMessageFunctionArguments
    
    def __init__(self, llm=OpenAILLM()):
        super().__init__()
        self.llm = llm

    def run(self, uid: str, message: str):
        raise NotImplementedError()
        # return a generator from openai
        # stream message to the user