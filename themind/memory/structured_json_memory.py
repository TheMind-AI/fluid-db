import os
import json
import jsonpath_ng
from pydantic import BaseModel, Field
from jsonpath_ng import parse
from genson import SchemaBuilder
from themind.memory.memory_base import MemoryBase
from themind.llm.openai_llm import OpenAILLM
import jsonpath_ng.ext


class JsonPathExpr(BaseModel):
    jsonPath: str = Field(..., description="Valid JSON path to query a JSON object based on the provided schema.")


class StructuredJsonMemory(MemoryBase):

    def __init__(self):
        self.memory = {}

        self.llm = OpenAILLM()

    def get_memory(self, uid: str):
        if uid not in self.memory:
            self._load_memory(uid)
        return self.memory[uid]

    def query(self, uid: str, json_path: str) -> list:

        try:
            jsonpath_expr = self.load_maybe_repair_jsonpath_expr(uid, json_path)
        except Exception as e:
            return f"Invalid JSONPath query: {json_path}, error {e}."
        
        matches = [match.value for match in jsonpath_expr.find(self.get_memory(uid))]

        return matches
    
    def load_maybe_repair_jsonpath_expr(self, uid: str, query: str) -> str:

        # jsonpath_ng does not support single quotes, bard said
        query = query.replace("'", '"')

        try:
            print(query)
            jsonpath_expr = jsonpath_ng.ext.parse(query)
        except Exception as e:
            prompt = f"""Invalid JSONPath query: {query}, error {e}. 
            Please enter a valid JSONPath query based on this json schema: {self.schema(uid)}"""
            response_model = self.llm.instruction_instructor(prompt, JsonPathExpr)
            assert isinstance(response_model, JsonPathExpr)
            try:
                jsonpath_expr = jsonpath_ng.ext.parse(response_model.jsonPath)
            except Exception as e:
                raise ValueError(f"Invalid JSONPath query: {response_model.jsonPath}, error {e}.")

        return jsonpath_expr

    def schema(self, uid: str):
        builder = SchemaBuilder()
        
        builder.add_object(self.get_memory(uid))
        
        schema = builder.to_schema()
    
        self._remove_required(schema)
        print(json.dumps(schema, indent=4))
        
        schema = self._compress_schema(schema)
        print(json.dumps(schema, indent=4))
        
        return schema
    
    def update(self, uid: str, path: str, new_data: dict):
        pass

    def query_lang_prompt(self) -> str:
        return "For querying the memory, always use jsonPath. For example, to query all baz values in this json: {'foo': [{'baz': 1}, {'baz': 2}]} use foo[*].baz as the query parameter"

    def add_value(self, uid: str, path: str, value: any):
        keys = path.split('.')
        temp = self.get_memory(uid)

        for key in keys[:-1]:
            temp = temp.setdefault(key, {})

        temp[keys[-1]] = value

        self._save_memory(uid, temp)

    def append_to_list(self, uid: str, path: str, value: any):
        keys = path.split('.')
        temp = self.get_memory(uid)

        for key in keys[:-1]:
            temp = temp.setdefault(key, {})

        if keys[-1] in temp:
            if isinstance(temp[keys[-1]], list):
                temp[keys[-1]].append(value)
            else:
                raise ValueError(f"Value at {path} is not a list.")
        else:
            temp[keys[-1]] = [value]

        self._save_memory(uid, temp)

    def _load_memory(self, uid: str):
        file_path = self._memory_file_path(uid)
        if os.path.exists(file_path):
            with open(file_path, 'r') as f:
                self.memory[uid] = json.load(f)
        else:
            self.memory[uid] = {}
    
    def _memory_file_path(self, uid: str):
        base_dir = os.path.dirname(os.path.realpath(__file__))
        file_path = os.path.join(base_dir, "data", f"{uid}.json")

        return file_path
    
    def _save_memory(self, uid: str, new_memory: dict):
        file_path = self._memory_file_path(uid)
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, 'w') as f:
            json.dump(new_memory, f, indent=4)

    def _compress_schema(self, schema):
        compressed_schema = {}
        for key, value in schema.items():
            if key == 'properties':
                for k, v in value.items():
                    if 'properties' in v or 'items' in v:
                        compressed_schema[k] = self._compress_schema(v)
                    elif isinstance(v, dict):
                        compressed_schema[k] = v.get('type')
            elif key == 'items' and isinstance(value, dict):
                compressed_schema = [self._compress_schema(value)]
        return compressed_schema

    def _remove_required(self, schema):
        schema.pop('required', None)
        for value in schema.get('properties', {}).values():
            if 'properties' in value:
                self._remove_required(value)
            if 'items' in value:
                self._remove_required(value['items'])


if __name__ == "__main__":
    uid = '1'

    memory = StructuredJsonMemory()

    res = memory.query(uid, "name")
    memory.append_to_list(uid, "events", {
            "location": "",
            "time": "18:00",
            "theme": "AI"
        })
    memory.append_to_list(uid, "test_list", {"location": "Golden Gate", "time": "12:00"})
    
    res = memory.query(uid, "events")
    print(res)
    
    print(memory.get_memory(uid))
    print(memory.schema(uid))
