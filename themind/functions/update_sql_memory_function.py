import json
from datetime import datetime, timedelta
from typing import Type, List
from pydantic import BaseModel, Field, field_validator
from themind.llm.openai_llm import OpenAILLM
from themind.functions.function_base import FunctionBase
from themind.memory.structured_json_memory import StructuredJsonMemory
from themind.memory.structured_sql_memory import StructuredSQLMemory


class SQLQueryModel(BaseModel):
    reasoning: str = Field(..., description="Max 100 character compressed reasoning for the answer")
    sql_queries: List[str] = Field(..., description="SQL Queries to execute")

    # TODO: SQL Query validation



class UpdateSQLMemoryFunctionArguments(BaseModel):
    reasoning: str = Field(..., description="Max 100 character compressed reasoning for the answer")
    user_message: str = Field(..., description="The data in human language that should be stored")


class UpdateSQLMemoryFunction(FunctionBase):
    name: str = "update-memory"
    description: str = "Tool to update or write to the structured memory of the user."
    args_schema: Type[BaseModel] = UpdateSQLMemoryFunctionArguments

    def __init__(self):
        super().__init__()
        self.llm = OpenAILLM()
        
    def run(self, uid: str, user_message: str):

        print("RUN, user_message:", user_message)

        memory = StructuredSQLMemory()

        schema = memory.schema(uid)

        print("SCHEMA:")
        print(schema)

        # First fetch data
        if schema:
            fetch_result = self.maybe_fetch_data(user_message, schema)
            print("==FETCH==")
            print(" REASONING:", fetch_result.reasoning)
            print(" QUERIES:", fetch_result.sql_queries)
            prev_reasoning = fetch_result.reasoning
            fetched_data = [memory.query(uid, q) for q in fetch_result.sql_queries]
        else:
            prev_reasoning = ""
            fetched_data = ""

        schema = memory.schema(uid)

        llm_result = self.maybe_update_memory(user_message, schema, fetched_data, prev_reasoning)
        print("==UPDATE==")
        print(" REASONING:", llm_result.reasoning)
        print(" QUERIES:", llm_result.sql_queries)

        update = [memory.query(uid, q) for q in llm_result.sql_queries]

        print(update)
        print("Done")

    # REMINDER: we'll need to deal with timezones here
    def maybe_update_memory(self, user_message: str, memory_schema: str, fetched_data=None, prev_reasoning: str = None) -> SQLQueryModel:

        prompt = self._update_memory_prompt(user_message, memory_schema, fetched_data, prev_reasoning)

        model = self.llm.instruction_instructor(prompt, SQLQueryModel, max_retries=3)
        assert isinstance(model, SQLQueryModel)

        return model

    def maybe_fetch_data(self, user_message: str, memory_schema: str) -> SQLQueryModel:

        prompt = self._retrieve_memory_prompt(user_message, memory_schema)

        model = self.llm.instruction_instructor(prompt, SQLQueryModel, max_retries=3)
        assert isinstance(model, SQLQueryModel)

        return model

    @staticmethod
    def _retrieve_memory_prompt(user_message: str, memory_schema: str = ""):
        return f"""
        You are a senior SQL master, AI that generates SQL Queries from natural language. You're using SQL for sqlite3 to query the database.
        
        Current datetime is {datetime.now().strftime("%Y-%m-%d %H:%M")}
        
        For the given request, return the list of SQL SELECT queries that retrieve the most relevant information from the sqlite database.
        You don't know what's in the data, write multiple queries to get as much relevant info as possible.
        ALWAYS write SELECT queries that support the database schema.
        ALWAYS fetch the whole row (*) with the SELECT statement, not just a single column.
        When filtering using strings, use LIKE to maximize chances of finding the data. More data is always better.
        If the data you're asked for are clearly not in the schema, return an empty string.
        
        Always run an internal dialogue before returning the query.
        
        ---
        
        SQL TABLES SCHEMA:
        {memory_schema if memory_schema else "There are no tables in the DB."}
        
        USER REQUEST: {user_message}
        """

    @staticmethod
    def _update_memory_prompt(user_message: str, memory_schema: str, fetched_data=None, prev_reasoning: str = None):
        return f"""
        You are a senior SQL database architect, AI that creates the best schema for data provided using SQL for sqlite3.
        
        Current datetime is {datetime.now().strftime("%Y-%m-%d %H:%M")}
        
        For the given user request you will return the list of SQL queries that store the information to sqlite database.
        ALWAYS think step by step. Run an internal dialogue before returning the queries.
        
        You receive the user request. First, think about how to store the data based on the database schema.
        If the data conform to the schema simply insert the new data using INSERT INTO statement.
        If you need new columns make sure to create them first using ALTER TABLE ADD COLUMN. Then make sure to INSERT the data in the next query. The order of queries matters!
        If the data needs a new table make sure to create the new table first using CREATE TABLE. Then make sure to INSERT the data in the next query. The order of queries matters!
        Make sure to keep the relationships between the tables using the correct ids.
        If you don't get relevant data in the prompt assume there are none and INSERT all data as they're new. If you have relevant data you can update the existing data using UPDATE queries.
        
        ALWAYS remember to insert the data if you created new table or added columns. If you don't store the data in one of the queries the data will be lost forever!

        ---
        SQL TABLES SCHEMA:
        {memory_schema if memory_schema else "No tables yet in the DB."}
        
        {f"Initial thoughts: {prev_reasoning}" if prev_reasoning else ""}
        
        {f"RELEVANT DATA FROM sqlite DB: {fetched_data}" if fetched_data else ""}
        
        USER REQUEST: {user_message}
        """
