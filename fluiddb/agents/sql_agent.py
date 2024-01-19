from fluiddb.agents.db_agent import DBAgent
from fluiddb.functions.update_sql_memory_function import UpdateSQLMemoryFunction, UpdateSQLMemoryFunctionArguments
from fluiddb.memory.structured_sql_memory import StructuredSQLMemory


class SQLAgent(DBAgent):
    def __init__(self):
        self.update_memory_function = UpdateSQLMemoryFunction()
        self.memory = StructuredSQLMemory()

    def save(self, text: str):
        args = UpdateSQLMemoryFunctionArguments(user_message=text, reasoning="")
        self.update_memory_function.run(uid="default", user_message=args)

    def maybe_save(self, text: str):
        # TODO: Implement a condition to decide whether to save or not
        self.save(text)

    def fetch(self, query: str, metadata: dict = {}):
        schema = self.memory.schema("default")
        fetch_result = self.update_memory_function.maybe_fetch_data(query, schema)
        fetched_data = [self.memory.query("default", q) for q in fetch_result.sql_queries]
        return fetched_data
