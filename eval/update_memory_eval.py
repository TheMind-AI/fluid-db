import json
from datetime import datetime

from fluiddb.functions.update_memory_function import UpdateMemoryFunction
from fluiddb.functions.update_sql_memory_function import UpdateSQLMemoryFunction
from fluiddb.database.json.json_engine import StructuredJsonMemory
from fluiddb.database.sql.structured_sql_memory import StructuredSQLMemory


class UpdateMemoryEval:

    def __init__(self):
        self.uid = datetime.now().strftime('%Y-%m-%d-%H-%M-%S')
        # self.uid = "2023-12-14-14-58-36"

    def run(self):
        # func = UpdateMemoryFunction()
        func = UpdateSQLMemoryFunction()

        func.run(self.uid, "Adams phone number is 722238738")
        func.run(self.uid, "David Mokos phone is 733544390")
        func.run(self.uid, "David Mokos phone is 733544390. David's phone is also 6286884994, it's a US phone")

        func.run(self.uid, "Tomorrow I have a history test I need to learn for.")
        func.run(self.uid, "Davids birthday is September 2")
        func.run(self.uid, "Adam likes riding big black horses")
        func.run(self.uid, "Adams last name is Zvada")
        func.run(self.uid, "Adam Zvada, the only Adam I know, lives in Prague and SF, Cali")

        func.run(self.uid, "Cool resource on RAG by LLamaIndex, best practicies https://docs.google.com/presentation/d/1IJ1bpoLmHfFzKM3Ef6OoWGwvrwDwLV7EcoOHxLZzizE/edit?usp=sharing")
        func.run(self.uid, "Gold nugget on how to find influencers by David Park https://x.com/Davidjpark96/status/1733739827508777305?s=20")

        print("FINAL MEMORY")
        StructuredSQLMemory().dump(self.uid)


if __name__ == '__main__':
    UpdateMemoryEval().run()