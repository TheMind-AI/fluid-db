import json
from datetime import datetime, timedelta
from typing import Type, List
from pydantic import BaseModel, Field, field_validator
from themind.llm.openai_llm import OpenAILLM
from themind.functions.function_base import FunctionBase
from themind.memory.structured_json_memory import StructuredJsonMemory


class UpdateMemoryModel(BaseModel):
    reasoning: str = Field(..., description="Max 100 character compressed reasoning for the answer")
    json_path: str = Field(..., description="JsonPath path where the data will be updated/added")
    data: str = Field(..., description="Data that will be updated or written to the specified jsonpath. MUST be a valid json.")
    description: str = Field(..., description="Description of the newly created fields/objects/arrays")

    @classmethod
    @field_validator("json_path")
    def validate_json_path(cls, v):
        try:
            StructuredJsonMemory.parse_jsonpath_expr(v)
        except ValueError as e:
            raise e
        return v

    @classmethod
    @field_validator("data")
    def validate_data(cls, v):
        try:
            json.loads(v)
        except Exception as e:
            raise e
        return v

class FetchMemoryModel(BaseModel):
    reasoning: str = Field(..., description="Max 100 character compressed reasoning for the answer")
    json_path_list: List[str] = Field(..., description="List of JsonPath expressions for fetching relevant data")

    @classmethod
    @field_validator("json_path_list")
    def validate_json_path_list(cls, v):
        if type(v) != list or len(v) == 0:
            raise ValueError("json_path_list is not a valid list. It has to abe a list with at least one jsonpath expression")
        try:
            [StructuredJsonMemory.parse_jsonpath_expr(expr) for expr in v]
        except ValueError as e:
            raise e
        return v



class UpdateMemoryFunctionArguments(BaseModel):
    reasoning: str = Field(..., description="Max 100 character compressed reasoning for the answer")
    user_message: str = Field(..., description="The data in human language that should be stored")


class UpdateMemoryFunction(FunctionBase):
    name: str = "update-memory"
    description: str = "Tool to update or write to the structured memory of the user."
    args_schema: Type[BaseModel] = UpdateMemoryFunctionArguments

    def __init__(self):
        super().__init__()
        self.llm = OpenAILLM()
        
    def run(self, uid: str, user_message: str):

        print("RUN, user_message:", user_message)

        memory = StructuredJsonMemory()

        schema = memory.schema(uid)
        schema_descriptions = memory.get_descriptions(uid)

        print("SCHEMA:")
        print(json.dumps(schema, indent=2))
        print(json.dumps(schema_descriptions, indent=2))

        # First fetch data
        fetch_result = self.maybe_fetch_data(user_message, schema, schema_descriptions)
        print("==FETCH==")
        print(" REASONING:", fetch_result.reasoning)
        print(" JSON PATH LIST:", fetch_result.json_path_list)

        fetched_data = {json_path: memory.query(uid, json_path) for json_path in fetch_result.json_path_list}


        llm_result = self.maybe_update_memory(user_message, schema, fetched_data, schema_descriptions)
        print("==UPDATE==")
        print(" REASONING:", llm_result.reasoning)
        print(" JSON PATH:", llm_result.json_path)
        print(" DATA:", llm_result.data)
        print(" DESCRIPTION:", llm_result.description)


        data = json.loads(llm_result.data)

        memory.update(uid, llm_result.json_path, data, llm_result.description)

        print("Done")

    # REMINDER: we'll need to deal with timezones here
    def maybe_update_memory(self, user_message: str, memory_schema: str, fetched_data=None, schema_descriptions: str = "") -> UpdateMemoryModel:

        prompt = self._update_memory_prompt(user_message, memory_schema, fetched_data, schema_descriptions)

        model = self.llm.instruction_instructor(prompt, UpdateMemoryModel, max_retries=3)
        assert isinstance(model, UpdateMemoryModel)

        return model

    def maybe_fetch_data(self, user_message: str, memory_schema: str, schema_descriptions: str = "") -> FetchMemoryModel:

        prompt = self._retrieve_memory_prompt(user_message, memory_schema, schema_descriptions)

        model = self.llm.instruction_instructor(prompt, FetchMemoryModel, max_retries=3)
        assert isinstance(model, FetchMemoryModel)

        return model

    @staticmethod
    def _retrieve_memory_prompt(user_message: str, memory_schema: str, schema_descriptions: str = ""):
        return f"""
        You are a query builder, AI that generates JsonPath query from natural language. You're using jsonpath-ng to query the structured memory.
        
        Current datetime is {datetime.now().strftime("%Y-%m-%d %H:%M")}
        
        The jsonpath-ng uses the following language:
        - Nested object: $.objects.nested_object
        - Array: $.objects.some_array[*]
        - Sorted: $.objects[\\some_field], $.objects[\\some_field,/other_field]
        - Filter: $.objects[?some_field =~ "foobar"], $.objects[?(@some_field > 5)] (make sure to put strings and regex into quotes)
        
        You receive a json model schema and a description of the data you need to fetch and you return jsonpath queries for relevant data.
        Because you don't know what's in the data, write multiple queries to get as much relevant info as possible, trying to filter based on different strings etc. You can use regex match (=~) to maximize chances of finding the data.
        ALWAYS write queries that support the JSON schema. NEVER query key/values which are not present in the provided json schema.
        Always fetch the whole object from an array, not just a single value. For example, if you're asked for the name of the user, don't return only the name, return the whole object.
        
        If the data you're asked for are not in the schema, return an empty array []
        Always expect date in this format: YYYY-MM-DD
        Always expect time in this format: HH:MM 
        
        Always run an internal dialogue before returning the query.

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
        
        REQUEST:
        What's Adam's phone number?
        
        QUERY:
        $.phones[?name = "adam"].number
        
        REQUEST:
        What events are happening tomorrow?
        
        QUERY:
        $.events[?(@.date = "2023-12-08")]

        ---
        
        SCHEMA:
        {memory_schema}
        {schema_descriptions if schema_descriptions else ""}
        
        REQUEST: {user_message}
        
        """

    @staticmethod
    def _update_memory_prompt(user_message: str, memory_schema: str, fetched_data=None, schema_descriptions: str = ""):
        return f"""
        You are a senior database architect, that creates queries and new data structures from natural language.
        
        Current datetime is {datetime.now().strftime("%Y-%m-%d %H:%M")}
        
        Take the message you received from the user and create a query and data to store in the structured memory.
        Try to append data to the existing schema where possible.
        When it's not possible to fit the data to the current schema make sure to include the description of the new field you create.
        
        Always think about using the memory in the future. You should create lists when we might append more objects of similar type in the future. To create a list the data should be a list: [new data]
        Don't put the jsonpath in the data, the object will be automatically created on the path you specify.
        Always use strings in lowercase when querying and filtering based on values. If you're comparing strings, use regex match: =~ to maximize chances of finding the data.
        
        If there are similar data in the schema but the data don't fit the current schema, create a new schema and make it inconsistent. Make sure to write a description of the new object schema.
        
        Always store date in this format: YYYY-MM-DD
        Always store time in this format: HH:MM

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
        QUERY: $.phones
        DATA: {{"name": "adam", "number": "722264238"}}

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
        
        ---
        
        These are some relevant data from the memory:
        {fetched_data}
        
        SCHEMA:
        {memory_schema}
        {schema_descriptions if schema_descriptions else ""}
        
        REQUEST: {user_message}
        """
