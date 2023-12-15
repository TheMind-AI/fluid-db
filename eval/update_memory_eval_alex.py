import json
import pandas as pd
from typing import List
from datetime import datetime
from themind.functions.update_memory_function import UpdateMemoryFunction
from themind.functions.update_sql_memory_function import UpdateSQLMemoryFunction
from themind.memory.structured_json_memory import StructuredJsonMemory
from themind.memory.structured_sql_memory import StructuredSQLMemory


class UpdateMemoryEval:

    def __init__(self):
        self.uid = datetime.now().strftime('%Y-%m-%d-%H-%M-%S')
        # self.uid = "2023-12-14-14-58-36"

    def run(self, sentences: List[str]):
        # func = UpdateMemoryFunction()
        func = UpdateSQLMemoryFunction()

        for sentence in sentences:
            func.run(self.uid, sentence)

        print("FINAL MEMORY")
        StructuredSQLMemory().dump(self.uid)


if __name__ == '__main__':

    file_path = '../data/alex-rivera-ground-truth.csv'

    df = pd.read_csv(file_path)
    sentences = df['Biography'].tolist()

    UpdateMemoryEval().run(sentences)