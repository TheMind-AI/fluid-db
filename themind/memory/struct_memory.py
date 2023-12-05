import os
import json
import pydantic

class StructuredMemory:


    def __init__(self, id: str):
        self.id = id
        self.memory = {}
        self.schema = ""
        self._load_memory(id)


    def _load_memory(self):
        file_path = self._memory_file_path()
        if os.path.exists(file_path):
            with open(file_path, 'r') as f:
                self.memory = json.load(f)
        else:
            with open(file_path, 'w') as f:
                json.dump({}, f)


    

    
    def _memory_file_path(self):
        base_dir = os.path.dirname(os.path.realpath(__file__))
        file_path = os.path.join(base_dir, "data", f"{self.id}.json")

        return file_path