from fluiddb.agents.db_agent import DBAgent
from pymongo import MongoClient
from fluiddb.functions.update_mongo_memory_function import UpdateMongoMemoryFunction, UpdateMongoMemoryFunctionArguments
from fluiddb.memory.structured_mongo_memory import StructuredMongoMemory


class JSONAgent(DBAgent):
    
    def __init__(self):
        pass

    def save(self, text: str):
        pass
    
    def maybe_save(self, text: str):
        pass

    def fetch(self, query: str, metadata: dict = {}):
        pass

