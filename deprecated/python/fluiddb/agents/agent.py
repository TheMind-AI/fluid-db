import json
from typing import List
from themind.schema.thread import Thread
from themind.schema.message import Message
from themind.llm.openai_llm import OpenAILLM
from themind.schema.function import Function
from themind.prompts.agent_prompt import AgentPrompt
from themind.memory.structured_json_memory import StructuredJsonMemory
from themind.functions.send_message_function import SendMessageFunction
from themind.functions.fetch_memory_function import FetchMemoryFunction, FetchMemoryModel
from themind.functions.update_memory_function import UpdateMemoryFunction, UpdateMemoryModel

# DEPRECTED
class Agent(object):

    def __init__(self):
        self.structured_memory = StructuredJsonMemory()
        self.llm = OpenAILLM()
        
        self.update_memory_function = UpdateMemoryFunction()
        self.fetch_memory_function = FetchMemoryFunction()
        
        self.functions = [self.fetch_memory_function, self.update_memory_function]
        
        self.functions_map = {}
        for function in self.functions:
            self.functions_map[function.name] = function
            
    def run_v2(self, uid: str, thread: Thread):

        # results = self.run_fetch_memory(uid=uid, thread=thread)
        # if results:
        #     print('FETCHED: ', results)
        # else:
        #     print('NO FETCH RESULTS')
            
        self.run_update_memory(uid=uid, thread=thread)

        # if results:
        #     thread.add_message(message=Message.user_message(content=results))
        # else:
        #     thread.add_message(message=Message.user_message(content='No results found.'))
        #
        # print(thread.to_openai_messages())
        # response = self.llm.chat(messages=thread.to_openai_messages(), stream=True)
        # print(response)
        # # print the chat stream
        # for message in response:
        #     print(message, end='')

    def run_fetch_memory(self, uid: str, thread: Thread):
        fetch_memory_obj = self.fetch_memory_function.maybe_fetch_memory(
            user_message=thread.messages[-1].content, memory_schema=self.structured_memory.schema(uid=uid)
        )
        assert isinstance(fetch_memory_obj, FetchMemoryModel)

        print(fetch_memory_obj)

        results = self.fetch_memory_function.run(uid=uid, query=fetch_memory_obj.jsonpath_query)
            
        return results

    def run_update_memory(self, uid: str, thread: Thread):
        reason, query, data = self.update_memory_function.maybe_update_memory(
            user_message=thread.messages[-1].content, memory_schema=self.structured_memory.schema(uid=uid)
        )
        print(reason)
        print(query)
        print(data)

        self.structured_memory.update(uid=uid, json_path=query, new_data=data)
        print(self.structured_memory.get_memory(uid=uid))

    def run(self, uid: str, thread: Thread):

        memory_schema = self.structured_memory.schema(uid=uid)
        prompt = """This is a JSON schema representation of my structured memory: {memory_schema}
                It's important to write queries that support this JSON schema. Don't query key/values which are not present in this provided json schema.
                If a user presents you with new information that is not likely present in the JSON schema, update the schema to include the new information.
                The new data which will be added to the memory should be well organized, like senior database engineer would do it.
                The instraction is a python script how to update the memory.
                """.format(memory_schema=memory_schema)
        thread.add_message(message=Message.user_message(content=prompt))

        function_calls = []
        while SendMessageFunction().name not in [call.name for call in function_calls]:
            function_calls, response_text = self.think_next_step(uid=uid, thread=thread)

            if response_text:
                thread.add_message(message=Message.assistent_message(content=response_text))
                return response_text

            thread.add_message(message=Message.function_message(content='', functions=function_calls))
            
            for function_call in function_calls:
                function_call.args['uid'] = uid
                response = self.functions_map[function_call.name].run(**function_call.args)
                print(response)
                thread.add_message(message=Message.assistent_message(content=response))
        
        return response

    def think_next_step(self, uid: str, thread: Thread) -> List[Function]:

        messages = thread.to_openai_messages()
        function_calls, response_text = self.llm.choose_function_call(messages=messages, functions=self.functions)

        print(response_text)
        
        return function_calls, response_text


if __name__ == '__main__':
    
    agent = Agent()
    
    uid = 'test'
    #message = 'what exams do i have tomrrow? I like skateboarding.'
    #message = 'i will be working tommrrow because i need to send a report to my inverstors. They will be happy to see the report.'
    message = 'what should i work on tmrw?'
    message = """
    FluidDB is a database for AI apps.

    LLM OS - TheMemory is FluidDB. It's a Dynamic File System.

    In the Future you won’t be thinking which database to use, this is all abstracted away from you, AI takes care of it.

    Data has a structure and AI can infer it."""
    message = """
    Is it possible to run compressed chain of thought?

    Goal: reduce the number of generated tokens while still giving the LLM time to think

    Benefit: UX boost - better & faster AI replies
    """
    #message = 'fluiddb is a project i work on.'

    thread = Thread.new_with_system_prompt(uid)
    
    new_message = Message.user_message(content=message)
    thread.add_message(message=new_message)
    
    #agent.run(uid=uid, thread=thread)
    agent.run_v2(uid=uid, thread=thread)