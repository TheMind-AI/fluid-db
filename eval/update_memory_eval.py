import json
from datetime import datetime

from themind.functions.update_memory_function import UpdateMemoryFunction
from themind.memory.structured_json_memory import StructuredJsonMemory


class UpdateMemoryEval:

    def __init__(self):
        self.uid = datetime.now().strftime('%Y-%m-%d-%H-%M-%S')

    def run(self):
        func = UpdateMemoryFunction()

        func.run(self.uid, "Adams phone number is 722238738")
        func.run(self.uid, "David Mokos phone is 733544390")
        func.run(self.uid, "David Mokos phone is 733544390. David's phone is also 6286884994, it's a US phone")

        memory = StructuredJsonMemory().get_memory(self.uid)

        print("FINAL MEMORY")
        print(json.dumps(memory, indent=4))


if __name__ == '__main__':
    UpdateMemoryEval().run()