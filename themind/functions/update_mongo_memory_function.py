import json
from datetime import datetime, timedelta
from typing import Type, List
from pydantic import BaseModel, Field, field_validator
from themind.llm.openai_llm import OpenAILLM
from themind.functions.function_base import FunctionBase
from themind.memory.structured_json_memory import StructuredJsonMemory
from themind.memory.structured_sql_memory import StructuredSQLMemory


class MongoQueryModel(BaseModel):
    reasoning: str = Field(..., description="Full step by step reasoning, max 250 characters")
    mql_queries: List[str] = Field(..., description="Mongo MQL Queries to execute")

    # TODO: MQL Query validation


class UpdateMongoMemoryFunctionArguments(BaseModel):
    reasoning: str = Field(..., description="Max 100 character compressed reasoning for the answer")
    user_message: str = Field(..., description="The data in human language that should be stored")


class UpdateMongoMemoryFunction(FunctionBase):
    name: str = "update-memory"
    description: str = "Tool to update or write to the structured memory of the user."
    args_schema: Type[BaseModel] = UpdateMongoMemoryFunctionArguments

    def __init__(self):
        super().__init__()
        self.llm = OpenAILLM()
        
    def run(self, uid: str, user_message: str, prev_requests: str = None):

        print("RUN, user_message:", user_message)

        memory = StructuredSQLMemory()

        schema = memory.schema(uid)

        print("SCHEMA:")
        print(schema)

        # First fetch data
        if schema:
            fetch_result = self.maybe_fetch_data(user_message, schema, prev_requests=prev_requests)
            print("==FETCH==")
            print(" REASONING:", fetch_result.reasoning)
            print(" QUERIES:", fetch_result.mql_queries)
            prev_reasoning = fetch_result.reasoning
            fetched_data = [memory.query(uid, q) for q in fetch_result.mql_queries]
        else:
            prev_reasoning = ""
            fetched_data = ""

        schema = memory.schema(uid)

        llm_result = self.maybe_update_memory(user_message, schema, fetched_data, prev_reasoning=prev_reasoning, prev_requests=prev_requests)
        print("==UPDATE==")
        print(" REASONING:", llm_result.reasoning)
        print(" QUERIES:", llm_result.mql_queries)

        update = [memory.query(uid, q) for q in llm_result.mql_queries]

        print(update)
        print("Done")

    # REMINDER: we'll need to deal with timezones here
    def maybe_update_memory(self, user_message: str, memory_schema: str, fetched_data=None, prev_reasoning: str = None, prev_requests: str = None) -> MongoQueryModel:

        prompt = self._update_memory_prompt(user_message, memory_schema, fetched_data, prev_reasoning, prev_requests)

        model = self.llm.instruction_instructor(prompt, MongoQueryModel, max_retries=3)
        assert isinstance(model, MongoQueryModel)

        return model

    def maybe_fetch_data(self, user_message: str, memory_schema: str, prev_requests: str = None) -> MongoQueryModel:

        prompt = self._retrieve_memory_prompt(user_message, memory_schema, prev_requests)

        model = self.llm.instruction_instructor(prompt, MongoQueryModel, max_retries=3)
        assert isinstance(model, MongoQueryModel)

        return model

    @staticmethod
    def _retrieve_memory_prompt(user_message: str, memory_schema: str = "", prev_requests: str = None):
        return f"""
        You are a senior MongoDB Database Architect, AI that generates MQL Mongo Queries from natural language. You're using MQL for MongoDB to query the database.
        
        Current datetime is {datetime.now().strftime("%Y-%m-%d %H:%M")}
        
        For the given request, return the list of MQL queries that retrieve the most relevant documents from MongoDB.
        You don't know what's in the data, write multiple queries to get as many relevant documents as possible.
        ALWAYS write queries that support the MONGODB COLLECTIONS SCHEMA, never make educated guesses.
        NEVER SELECT by fields that do not exist in MONGODB COLLECTIONS SCHEMA, such query would kill innocent people.
        ALWAYS fetch the whole document with the, not just a single field.
        When filtering using strings, use regex to maximize chances of finding the data. More data is always better.
        If the data you're asked for are clearly not in the schema, return an empty string.
        
        Always run an internal dialogue before returning the query.
        
        ---
        
        PREVIOUS USER REQUESTS:
        {prev_requests if prev_requests else "None"}
        
        MONGODB COLLECTIONS SCHEMA:
        {memory_schema if memory_schema else "There are no collections in MongoDB."}
        
        USER REQUEST: {user_message}
        """

    @staticmethod
    def _update_memory_prompt(user_message: str, memory_schema: str, fetched_data=None, prev_reasoning: str = None, prev_requests: str = None):
        return f"""
        You are a senior MongoDB Database Architect, AI that creates the best schema for data provided using MQL for MongoDB.
        
        Current datetime is {datetime.now().strftime("%Y-%m-%d %H:%M")}
        
        For the given user request you will return the list of MongoDB MQL queries that store the information to MongoDB database.
        ALWAYS think step by step. Run an internal dialogue before returning the queries.
        
        You receive the user request. First, think about how to store the data based on the MongoDB collection schema.
        If there is the right collection for the data, insert them. Try to stick to the schema.
        If the data needs a new collection make sure to create the collection first.
        
        
        ---
        PREVIOUS USER REQUESTS:
        {prev_requests if prev_requests else "None"}
        
        MONGODB COLLECTIONS SCHEMA:
        {memory_schema if memory_schema else "There are no collections in MongoDB."}
        
        {f"Initial thoughts: {prev_reasoning}" if prev_reasoning else ""}
        
        {f"RELEVANT DATA FROM MongoDB: {fetched_data}" if fetched_data else ""}
        
        USER REQUEST: {user_message}
        """
