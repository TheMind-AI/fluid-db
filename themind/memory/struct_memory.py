import os
import json
from jsonpath_ng import parse
from genson import SchemaBuilder

class StructuredMemory:


    def __init__(self, id: str):
        self.id = id
        self.memory = {}
        self.schema = ""
        self._load_memory()

    def add_value(self, path: str, value: any):
        keys = path.split('.')
        temp = self.memory

        for key in keys[:-1]:
            temp = temp.setdefault(key, {})

        temp[keys[-1]] = value

        self._save_memory()

    def append_to_list(self, path: str, value: any):
        keys = path.split('.')
        temp = self.memory

        for key in keys[:-1]:
            temp = temp.setdefault(key, {})

        if keys[-1] in temp:
            if isinstance(temp[keys[-1]], list):
                temp[keys[-1]].append(value)
            else:
                raise ValueError(f"Value at {path} is not a list.")
        else:
            temp[keys[-1]] = [value]

        self._save_memory()

    def get_schema(self):
        builder = SchemaBuilder()
        builder.add_object(self.memory)
        schema = builder.to_schema()
        self._remove_required(schema)
        print(json.dumps(schema, indent=4))
        schema = self._compress_schema(schema)
        print(json.dumps(schema, indent=4))
        return schema

    def _load_memory(self):
        file_path = self._memory_file_path()
        if os.path.exists(file_path):
            with open(file_path, 'r') as f:
                self.memory = json.load(f)
    
    def _memory_file_path(self):
        base_dir = os.path.dirname(os.path.realpath(__file__))
        file_path = os.path.join(base_dir, "data", f"{self.id}.json")

        return file_path
    
    def _save_memory(self):
        with open(self._memory_file_path(), 'w') as f:
            json.dump(self.memory, f, indent=4)

    def query(self, jsonPath: str) -> list:
        jsonpath_expr = parse(jsonPath)
        matches = [match.value for match in jsonpath_expr.find(self.memory)]

        return matches
    
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
    memory = StructuredMemory("1")
    res = memory.query("name")
    # memory.append_to_list("events", {
    #         "location": "",
    #         "time": "18:00",
    #         "theme": "AI"
    #     })
    # memory.append_to_list("test_list", {"location": "Golden Gate", "time": "12:00"})
    
    print(memory.memory)
    print(memory.get_schema())
