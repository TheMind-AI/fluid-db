from typing import Type
from pydantic import BaseModel, Field
from datetime import datetime
from themind.llm.openai_llm import OpenAILLM
from themind.functions.function_base import FunctionBase
from themind.retrievers.dummy_retriever import DummyRetriever
from themind.retrievers.retriever_base import RetrieverBase


class FetchMemoryModel(BaseModel):
    reasoning: str = Field(..., description="Max 100 character compressed reasoning for the answer")
    jsonpath_query: str = Field(..., description="jsonpath-ng query to fetch memory based on the provided memory JSON schema")


class FetchMemoryFunctionArguments(BaseModel):
    query: str = Field(..., description="Query to fetch memory")


class FetchMemoryFunction(FunctionBase):
    
    name: str = "retrieve-memory"
    description: str = """
    Tool to retrieve memories from the user's structured memory. 
    It uses JSONPath to query the memory; write the query based on the provided JSON schema. 
    It's important to write queries that support this JSON schema. 
    Don't query key/values which are not present in this provided json schema. 
    If the memory scheme is empty {}, don't query it!
    """
    args_schema: Type[BaseModel] = FetchMemoryFunctionArguments
    
    def __init__(self, retriever: RetrieverBase = DummyRetriever()):
        super().__init__()
        self.retriever = retriever
        self.llm = OpenAILLM()

    def run(self, uid: str, query: str):
        if query == 'NA':
            return None
        return self.retriever.retrieve(uid=uid, query=query)
    
    # REMINDER: we'll need to deal with timezones here
    def maybe_fetch_memory(self, user_message: str, memory_schema: str) -> FetchMemoryModel:
        print(memory_schema)
        prompt = f"""
        You are a query builder, AI that generates JsonPath query from natural language. You're using jsonpath-ng to query the structured memory.
        
        You receive a json schema and a natural description of the data you need to fetch and you return the jsonpath query based on the model.
        It's important to write queries that support this JSON schema. Don't query key/values which are not present in this provided json schema.
        
        Always use strings in lowercase when querying and filtering based on values. If you're comparing strings, use regex match: =~ to maximize chances of finding the data.

        If the data you're asked for are not in the schema, only reply "NA"
        
        Don't fetch only only one key/value, always fetch the whole object. For example, if you're asked for the name of the user, don't return only the name, return the whole object.
        
        Store date data always in format this format: YYYY-MM-DD
        Store time data always in format this format: HH:MM 
        Today is {datetime.now().strftime("%Y-%m-%d")}
        
        Always run an internal dialogue before returning the query.

        Here is the user memory JSON schema:
        {memory_schema}
        
        Here is the user message:
        {user_message}
        
        ---

        Examples:
        
        SCHEMA:
        {{
          "phones": [
            {{
              "name": "string",
              "number": "string"
            }}
          ],
          "user": {{
            "name": "string"
          }},
          "events": [
            {{
              "name": "string",
              "date": "string",
              "price": "number"
            }}
          ]
        }}
        
        QUESTION:
        What's Adam's phone number?
        
        QUERY:
        $.phones[?name = "adam"].number
        
        QUESTION:
        What events are happening tomorrow?
        
        QUERY:
        $.events[?date = "2023-12-08"] or
        $.events[?(@.date = "2023-12-08")]
        
        QUESTION:
        What do all upcoming events cost?
        
        QUERY:
        $.events[?(@.date >= "2023-12-07")].price
        
        """
        return self.llm.instruction_instructor(prompt, FetchMemoryModel)



