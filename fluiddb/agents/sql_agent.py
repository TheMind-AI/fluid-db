from fluiddb.agents.db_agent import DBAgent
from fluiddb.functions.update_sql_memory_function import UpdateSQLMemoryFunction, UpdateSQLMemoryFunctionArguments
from fluiddb.memory.structured_sql_memory import StructuredSQLMemory


class SQLAgent(DBAgent):
    
    def __init__(self):
        pass

    def save(self, text: str):
        pass

    def maybe_save(self, text: str):
        pass
    
    def fetch(self, query: str, metadata: dict = {}):
        pass
