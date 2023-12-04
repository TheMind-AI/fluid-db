from themind.functions.function_base import FunctionBase


class SendMessageFunction(FunctionBase):

    def __init__(self):
        super().__init__()

    async def run(self, uid: str, message: str):
        pass
        # stream message to the user