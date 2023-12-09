import json
from datetime import datetime, timedelta
from typing import Type
from pydantic import BaseModel, Field
from themind.llm.openai_llm import OpenAILLM
from themind.functions.function_base import FunctionBase


class UpdateMemoryModel(BaseModel):
    reasoning: str = Field(..., description="Max 100 character compressed reasoning for the answer")
    jsonpath_query: str = Field(..., description="JSONPath query path where the data will be updated/added")
    data: str = Field(..., description="Data that will be updated or written to the specified jsonpath.")


class UpdateMemoryFunctionArguments(BaseModel):
    data: str = Field(..., description="Data that will be updated or written to the structured memory.")
    instruction: str = Field(..., description="Instructions on how to write to the structured memory based on the existing JSON memory schema.")


class UpdateMemoryFunction(FunctionBase):

    name: str = "update-memory"
    description: str = "Tool to update or write to the structured memory of the user."
    args_schema: Type[BaseModel] = UpdateMemoryFunctionArguments

    def __init__(self):
        super().__init__()
        self.llm = OpenAILLM()
        
    def run(self, uid: str, data: dict, instruction: str):

        print(data)
        print(instruction)

        raise 'Memory update'

    # REMINDER: we'll need to deal with timezones here
    def maybe_update_memory(self, user_message: str, memory_schema: str) -> UpdateMemoryModel:
        prompt = f"""
        You are a senior database architect, that creates queries and new data structures from natural language.
        
        Take the message you received from the user and create a query and data to store in the structured memory.
        
        You receive a json schema and a natural description of the data you need to store and you return the jsonpath and data to store based on the model.
        You return a jsonPath which is the location where the new data will be put and the new data object.
        Always think about using the memory in the future. You should create lists when we might append more objects of similar type in the future. To create a list the data should be a list: [new data]
        Don't put the jsonpath in the data, the object will be automatically created on the path you specify.
        Always use strings in lowercase when querying and filtering based on values. If you're comparing strings, use regex match: =~ to maximize chances of finding the data.
        
        If there are similar data in the schema but the data don't fit the current schema, create a new path for the new data with appendix "_new". 
        For example, appending object {{"name":"david", "relationship":''friend"}} to a list "relatives" of type ["string"] requires you to create a new list "relatives_new" with the first object [{{"name":"david", "relationship":''friend"}}]
        
        Store date data always in format this format: YYYY-MM-DD
        Store time data always in format this format: HH:MM 

        Always run an internal dialogue before returning the query and data.
        
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

        REQUEST: Adam's phone number is 722263238.
        QUERY: $.phones[?name = "adam"].number
        DATA: 722264238

        REQUEST: My last name is Zvada.
        QUERY: $.user.last_name
        DATA: Zvada

        REQUEST: Adam's phone number has +420 prefix.
        QUERY: $.phones[?name = "adam"].prefix
        DATA: +420

        REQUEST: I'm going to a Christmas party tomorrow which costs 20 usd to entry.
        QUERY: $.events_new
        DATA: {{"name": "Christmas party", "date": "{(datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')}", "price": {{"currency": "USD", "value": 20}}}}
        
        REQUEST: What is my brother's name?
        QUERY: NA
        DATA: {{}}
        ----
        
        SCHEMA:
        {memory_schema}
        
        REQUEST: {user_message}
        QUERY:
        DATA: 
        
        """
        model = self.llm.instruction_instructor(prompt, UpdateMemoryModel)
        assert isinstance(model, UpdateMemoryModel)
        
        data_dict = json.loads(model.data)
        
        return model.reasoning, model.jsonpath_query, data_dict
