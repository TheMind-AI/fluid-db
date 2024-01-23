from fluiddb.agents.db_agent import DBAgent
from pymongo import MongoClient
from fluiddb.functions.update_mongo_memory_function import UpdateMongoMemoryFunction, UpdateMongoMemoryFunctionArguments
from fluiddb.memory.structured_mongo_memory import StructuredMongoMemory
from fluiddb.databases.database_engine import DatbaseEngine
from fluiddb.databases.mongo import MongoEngine

class MongoAgent(DBAgent):
    
    def __init__(self):
        self.db_engine = MongoEngine()

    def save(self, text: str):
        self.db_engine.save(text=text)
    
    def maybe_save(self, text: str):
        self.db_engine.save(text=text)

    def fetch(self, query: str, metadata: dict = {}):
        self.db_engine.query(text=text)

