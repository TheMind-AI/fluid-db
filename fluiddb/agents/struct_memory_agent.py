from themind.schema.thread import Thread
from themind.llm.openai_llm import OpenAILLM
from themind.memory.structured_json_memory import StructuredJsonMemory
from themind.functions.fetch_memory_function import FetchMemoryFunction, FetchMemoryModel
from themind.functions.update_memory_function import UpdateMemoryFunction, UpdateMemoryModel


# DEPRECTED
class StructMemoryAgent(object):

    def __init__(self):
        self.structured_memory = StructuredJsonMemory()
        self.llm = OpenAILLM()
        
        self.update_memory_function = UpdateMemoryFunction()
        self.fetch_memory_function = FetchMemoryFunction()
        

    def retrieve_struct_memory(self, uid: str, thread: Thread):
        fetch_memory_obj = self.fetch_memory_function.maybe_fetch_memory(
            user_message=thread.messages[-1].content, memory_schema=self.structured_memory.schema(uid=uid)
        )
        assert isinstance(fetch_memory_obj, FetchMemoryModel)

        results = self.fetch_memory_function.run(uid=uid, query=fetch_memory_obj.jsonpath_query)
            
        return results

    def update_struct_memory(self, uid: str, thread: Thread):
        reason, query, data = self.update_memory_function.maybe_update_memory(
            user_message=thread.messages[-1].content, memory_schema=self.structured_memory.schema(uid=uid)
        )

        self.structured_memory.update(uid=uid, json_path=query, new_data=data)
