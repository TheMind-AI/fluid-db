import json
import pandas as pd
from typing import List
from datetime import datetime
from fluiddb.functions.update_memory_function import UpdateMemoryFunction
from fluiddb.functions.update_sql_memory_function import UpdateSQLMemoryFunction
from fluiddb.memory.structured_json_memory import StructuredJsonMemory
from fluiddb.memory.structured_sql_memory import StructuredSQLMemory


class UpdateMemoryEval:

    def __init__(self):
        self.uid = datetime.now().strftime('%Y-%m-%d-%H-%M-%S')
        # self.uid = "2023-12-14-14-58-36"

    def run(self, sentences: List[str]):
        # func = UpdateMemoryFunction()
        func = UpdateSQLMemoryFunction()

        for idx, sentence in enumerate(sentences):
            prev_sentences = "\n  ".join(sentences[:idx])
            func.run(self.uid, sentence, prev_requests=prev_sentences)

        print("FINAL MEMORY")
        StructuredSQLMemory().dump(self.uid)


if __name__ == '__main__':

    file_path = '../data/alex-rivera-ground-truth.csv'

    df = pd.read_csv(file_path)
    sentences = df['Biography'].tolist()

    UpdateMemoryEval().run(sentences)
