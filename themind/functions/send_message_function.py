from themind.functions.function_base import FunctionBase


class SendMessageFunction(FunctionBase):

    def __init__(self, llm: LLMBase = OpenAI):
        super().__init__()

    def run(self, uid: str, message: str):
        # return a generator from openai
        # stream message to the user