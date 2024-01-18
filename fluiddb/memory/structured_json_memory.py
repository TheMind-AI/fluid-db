import os
import json
import re

import jsonpath_ng
from genson import SchemaBuilder
from themind.llm.openai_llm import OpenAILLM
import jsonpath_ng.ext

class StructuredJsonMemory:

    def __init__(self):
        self.memory = {}
        self.llm = OpenAILLM()

    def get_memory(self, uid: str):
        if uid not in self.memory:
            self._load_memory(uid)
        return self.memory[uid]

    def query(self, uid: str, json_path: str) -> list:
        expr = self.parse_jsonpath_expr(json_path)
        matches = [match.value for match in expr.find(self.get_memory(uid))]
        return matches

    def update(self, uid: str, json_path: str, new_data: dict, data_description: str = "") -> dict:
        expr = self.parse_jsonpath_expr(json_path)

        memory = self.get_memory(uid)
        res = expr.find(memory)
        if res and type(res[0].value) == list:
            # Append to existing list
            new_new_data = res[0].value
            if type(new_data) == list:
                [new_new_data.append(d) for d in new_data]
            else:
                new_new_data.append(new_data)
            expr.update_or_create(memory, new_new_data)
        else:
            # Create new field/list
            expr.update_or_create(memory, new_data)

        self._save_memory(uid, memory)

        if data_description:
            self._save_description(uid, json_path, data_description)

        return memory

    def schema(self, uid: str):
        builder = SchemaBuilder()

        builder.add_object(self.get_memory(uid))

        schema = builder.to_schema()

        self._remove_required(schema)
        # print(json.dumps(schema, indent=4))

        try:
            schema = self._compress_schema(schema)
        except Exception as e:
            print(e)
            pass

        return schema

    def _save_description(self, uid: str, json_path: str, description: str):
        file_path = self._descriptions_file_path(uid)
        if os.path.exists(file_path):
            with open(file_path, 'r') as f:
                descriptions = json.load(f)
        else:
            descriptions = {}

        simple_path = self.simplify_jsonpath(json_path)
        expr = self.parse_jsonpath_expr(simple_path)
        expr.update_or_create(descriptions, description)

        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, 'w') as f:
            json.dump(descriptions, f, indent=2)

    def get_descriptions(self, uid: str):
        file_path = self._descriptions_file_path(uid)
        if os.path.exists(file_path):
            with open(file_path, 'r') as f:
                descriptions = json.load(f)
            return descriptions
        return {}

    @staticmethod
    def parse_jsonpath_expr(json_path: str):
        json_path = json_path.replace("'", '"')
        try:
            jsonpath_expr = jsonpath_ng.ext.parse(json_path)
        except Exception as e:
            raise ValueError(f"Invalid JsonPath query: {json_path}, error: {e}")

        return jsonpath_expr

    def _load_memory(self, uid: str):
        file_path = self._memory_file_path(uid)
        if os.path.exists(file_path):
            with open(file_path, 'r') as f:
                self.memory[uid] = json.load(f)
        else:
            self.memory[uid] = {}

    @staticmethod
    def _memory_file_path(uid: str):
        base_dir = os.path.dirname(os.path.realpath(__file__))
        file_path = os.path.join(base_dir, "data", f"{uid}.json")

        return file_path

    @staticmethod
    def _descriptions_file_path(uid: str):
        base_dir = os.path.dirname(os.path.realpath(__file__))
        file_path = os.path.join(base_dir, "data", f"{uid}-desc.json")

        return file_path

    @staticmethod
    def simplify_jsonpath(jsonpath: str):
        clean_path = jsonpath

        clean_path = re.sub(r'\[.*?\]', '', clean_path)
        clean_path = re.sub(r'`.*?`', '', clean_path)

        return clean_path
        #
        # parts = clean_path.lstrip('$').split('.')
        # simplified_parts = [part for part in parts if part]
        #
        # return simplified_parts

    def _save_memory(self, uid: str, new_memory: dict):
        file_path = self._memory_file_path(uid)
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, 'w') as f:
            json.dump(new_memory, f, indent=4)

    def _compress_schema(self, schema):
        compressed_schema = {}
        if type(schema) == list:
            return [self._compress_schema(v) for v in schema]

        if "type" in schema.keys():
            if schema["type"] == "object" and "properties" in schema:
                compressed_schema = self._compress_schema(schema["properties"])
            elif schema["type"] == "array" and "items" in schema:
                compressed_schema = [self._compress_schema(schema["items"])]
            elif schema["type"] == "object":
                compressed_schema = {}
            else:
                compressed_schema = schema["type"]
        else:
            for key, value in schema.items():
                compressed_schema[key] = self._compress_schema(value)
        return compressed_schema

    def _remove_required(self, schema):
        schema.pop('required', None)
        for value in schema.get('properties', {}).values():
            if 'properties' in value:
                self._remove_required(value)
            if 'items' in value:
                self._remove_required(value['items'])
