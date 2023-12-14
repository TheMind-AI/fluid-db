import json
from datetime import datetime

from themind.functions.update_memory_function import UpdateMemoryFunction
from themind.functions.update_sql_memory_function import UpdateSQLMemoryFunction
from themind.memory.structured_json_memory import StructuredJsonMemory
from themind.memory.structured_sql_memory import StructuredSQLMemory


class UpdateMemoryEval:

    def __init__(self):
        self.uid = datetime.now().strftime('%Y-%m-%d-%H-%M-%S')

    def run(self):
        # func = UpdateMemoryFunction()
        func = UpdateSQLMemoryFunction()

        func.run(self.uid, "Adams phone number is 722238738")
        func.run(self.uid, "David Mokos phone is 733544390")
        func.run(self.uid, "David Mokos phone is 733544390. David's phone is also 6286884994, it's a US phone")

        print("FINAL MEMORY")
        StructuredSQLMemory().dump(self.uid)


if __name__ == '__main__':
    UpdateMemoryEval().run()