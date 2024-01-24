from typing import Type, List, Optional
from datetime import datetime
from fluiddb.agents.db_agent import DBAgent
from fluiddb.llm.openai_llm import OpenAILLM
from pydantic import BaseModel, Field
from fluiddb.databases.sql.sql_engine import SQLEngine


class SQLQueryModel(BaseModel):
    reasoning: str = Field(..., description="Full step by step reasoning, max 250 characters")
    sql_queries: List[str] = Field(..., description="SQL Queries to execute")
    
    # TODO: SQL Query validation


class SQLAgent(DBAgent):
    
    def __init__(self, db_id: str):
        super().__init__()
        self.llm = OpenAILLM()
        self.db_engine = SQLEngine(db_id)

    def save(self, text: str, context: Optional[str] = None):
        
        schema = self.db_engine.schema()
        
        print("SCHEMA:")
        print(schema)

        # TODO: should be used fetch method
        if schema:
            fetch_result = self.maybe_fetch_data(text, schema, prev_requests=context)
            print("==FETCH==")
            print(" REASONING:", fetch_result.reasoning)
            print(" QUERIES:", fetch_result.sql_queries)
            prev_reasoning = fetch_result.reasoning
            fetched_data = [self.db_engine.query(q) for q in fetch_result.sql_queries]
        else:
            prev_reasoning = ""
            fetched_data = ""

        llm_result = self.maybe_update_memory(text, schema, fetched_data, prev_reasoning=prev_reasoning, prev_requests=context)
        print("==UPDATE==")
        print(" REASONING:", llm_result.reasoning)
        print(" QUERIES:", llm_result.sql_queries)

        update = [self.db_engine.query(q) for q in llm_result.sql_queries]

        print(update)
        print("Done")
    
    def fetch(self, query: str, context: Optional[str] = None, metadata: dict = {}):
        
        schema = self.db_engine.schema()
        
        if schema:
            fetch_result = self.maybe_fetch_data(query, schema, prev_requests=context)
            print("==FETCH==")
            print(" REASONING:", fetch_result.reasoning)
            print(" QUERIES:", fetch_result.sql_queries)
            prev_reasoning = fetch_result.reasoning
            fetched_data = [self.db_engine.query(q) for q in fetch_result.sql_queries]
        else:
            prev_reasoning = ""
            fetched_data = ""
    
    # REMINDER: we'll need to deal with timezones here
    def maybe_update_memory(self, user_message: str, memory_schema: str, fetched_data=None, prev_reasoning: str = None, prev_requests: str = None) -> SQLQueryModel:

        prompt = self._update_memory_prompt(user_message, memory_schema, fetched_data, prev_reasoning, prev_requests)

        model = self.llm.instruction_instructor(prompt, SQLQueryModel, max_retries=3)
        assert isinstance(model, SQLQueryModel)

        return model

    def maybe_fetch_data(self, user_message: str, memory_schema: str, prev_requests: str = None) -> SQLQueryModel:

        prompt = self._retrieve_memory_prompt(user_message, memory_schema, prev_requests)

        model = self.llm.instruction_instructor(prompt, SQLQueryModel, max_retries=3)
        assert isinstance(model, SQLQueryModel)

        return model

    @staticmethod
    def _retrieve_memory_prompt(user_message: str, memory_schema: str = "", prev_requests: str = None):
        return f"""
        You are a senior SQL master, AI that generates SQL Queries from natural language. You're using SQL for sqlite3 to query the database.
        
        Current datetime is {datetime.now().strftime("%Y-%m-%d %H:%M")}
        
        For the given request, return the list of SQL SELECT queries that retrieve the most relevant information from the sqlite database.
        You don't know what's in the data, write multiple queries to get as much relevant info as possible.
        ALWAYS write SELECT queries that support the SQL TABLES SCHEMA, never make educated guesses.
        NEVER SELECT columns that do not exist in SQL TABLES SCHEMA, such query would kill innocent people.
        ALWAYS fetch the whole row (*) with the SELECT statement, not just a single column.
        When filtering using strings, use LIKE to maximize chances of finding the data. More data is always better.
        If the data you're asked for are clearly not in the schema, return an empty string.
        
        Always run an internal dialogue before returning the query.
        
        ---
        
        PREVIOUS USER REQUESTS:
        {prev_requests if prev_requests else "None"}
        
        SQL TABLES SCHEMA:
        {memory_schema if memory_schema else "There are no tables in the DB."}
        
        USER REQUEST: {user_message}
        """

    @staticmethod
    def _update_memory_prompt(user_message: str, memory_schema: str, fetched_data=None, prev_reasoning: str = None, prev_requests: str = None):
        return f"""
        You are a senior SQL database architect, AI that creates the best schema for data provided using SQL for sqlite3.
        
        Current datetime is {datetime.now().strftime("%Y-%m-%d %H:%M")}
        
        For the given user request you will return the list of SQL queries that store the information to sqlite database.
        ALWAYS think step by step. Run an internal dialogue before returning the queries.
        
        You receive the user request. First, think about how to store the data based on the database schema.
        If the data conform to the schema simply insert the new data using INSERT INTO statement.
        If you need new columns make sure to create them first using ALTER TABLE ADD COLUMN. Then make sure to INSERT the data in the next query. The order of queries matters!
        NEVER make educated guesses, ONLY INSERT data if the columns exist in the SQL TABLES SCHEMA, otherwise create them first.
        You can't INSERT or UPDATE a column that does not exist yet. First you must ALTER TABLE ADD COLUMN. Otherwise an error will occur and innocent people will die.
        If the data needs a new table make sure to create the new table first using CREATE TABLE. Then make sure to INSERT the data in the next query. The order of queries matters!
        Make sure to keep the relationships between the tables using the correct ids.
        If you don't get relevant data in the prompt assume there are none and INSERT all data as they're new. If you have relevant data you can update the existing data using UPDATE queries.
        
        ALWAYS remember to insert the data if you created new table or added columns. If you don't store the data in one of the queries the data will be lost forever!

        ---
        PREVIOUS USER REQUESTS:
        {prev_requests if prev_requests else "None"}
        
        SQL TABLES SCHEMA:
        {memory_schema if memory_schema else "No tables yet in the DB."}
        
        {f"Initial thoughts: {prev_reasoning}" if prev_reasoning else ""}
        
        {f"RELEVANT DATA FROM sqlite DB: {fetched_data}" if fetched_data else ""}
        
        USER REQUEST: {user_message}
        """

