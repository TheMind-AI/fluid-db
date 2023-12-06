from typing import Type
from pydantic import BaseModel
from themind.llm.openai_llm import OpenAILLM
from themind.functions.function_base import FunctionBase


class SendMessageFunction(FunctionBase):

    
    name: str = "Retrive Memory"
    description: str = "Tool to retrive memory from the user"
    args_schema: Type[BaseModel] = None
    
    def __init__(self, llm=OpenAILLM()):
        super().__init__()
        self.llm = llm

    def run(self, uid: str, message: str):
        raise NotImplementedError()
        # return a generator from openai
        # stream message to the user