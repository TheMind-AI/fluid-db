from themind.schema.thread import Thread
from themind.llm.openai_llm import OpenAILLM
from themind.memory.structured_json_memory import StructuredJsonMemory
from themind.functions.fetch_memory_function import FetchMemoryFunction, FetchMemoryModel
from themind.functions.update_memory_function import UpdateMemoryFunction, UpdateMemoryModel


class FluidDBAgent(object):

    def __init__(self, db_engine):
        self.db_engine = db_engine
        if self.db_engine == 'mongo':
            from fluiddb.functions.update_mongo_memory_function import UpdateMongoMemoryFunction
            self.update_memory_function = UpdateMongoMemoryFunction()
        elif self.db_engine == 'sql':
            from fluiddb.functions.update_sql_memory_function import UpdateSQLMemoryFunction
            self.update_memory_function = UpdateSQLMemoryFunction()
        else:
            from fluiddb.functions.update_memory_function import UpdateMemoryFunction
            self.update_memory_function = UpdateMemoryFunction()

    def save(text: str):
        pass
    
    def fetch(query: str):
        pass


if __name__ == '__main__':

    fluid_db = FluidDBAgent()
    
    fluid_db.save("Adams phone number is 722238738")
    fluid_db.save("David Mokos phone is 733544390")
    fluid_db.save("David Mokos phone is 733544390. David's phone is also 6286884994, it's a US phone")
    fluid_db.save("Tomorrow I have a history test I need to learn for.")
    fluid_db.save("Davids birthday is September 2")
    fluid_db.save("Adam likes riding big black horses")
    fluid_db.save("Adams last name is Zvada")
    fluid_db.save("Adam Zvada, the only Adam I know, lives in Prague and SF, Cali")
    fluid_db.save("Cool resource on RAG by LLamaIndex, best practicies https://docs.google.com/presentation/d/1IJ1bpoLmHfFzKM3Ef6OoWGwvrwDwLV7EcoOHxLZzizE/edit?usp=sharing")
    fluid_db.save("Gold nugget on how to find influencers by David Park https://x.com/Davidjpark96/status/1733739827508777305?s=20")

    fluid_db.fetch('what is adams phone?')