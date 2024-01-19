from fluiddb.schema.thread import Thread
from fluiddb.llm.openai_llm import OpenAILLM
from fluiddb.database.json.json_engine import StructuredJsonMemory
from fluiddb.functions.fetch_memory_function import FetchMemoryFunction, FetchMemoryModel
from fluiddb.functions.update_memory_function import UpdateMemoryFunction, UpdateMemoryModel
from fluiddb.agents.db_agent import DBAgent


class FluidDB(object):

    def __init__(self, db_agent: DBAgent):
        self.db_agent = db_agent    
        # TODO: add datbase id to know to which db it should connect
        # TODO: will be exdecuted by vectorstore/colbert agent
    
    def save(self, text: str, force_save: bool = False):
        if force_save:
            self.db_agent.save(text)
        else:
            self.db_agent.maybe_save(text)
    
    def fetch(self, query: str, metadata: dict = {}):
        self.db_agent.fetch(query, metadata)


if __name__ == '__main__':

    fluid_db = FluidDB()
    
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
