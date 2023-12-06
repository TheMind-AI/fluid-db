from typing import List
from themind.schema.thread import Thread
from themind.schema.message import Message
from themind.llm.openai_llm import OpenAILLM
from themind.schema.function import Function
from themind.prompts.agent_prompt import AgentPrompt
from themind.memory.structured_json_memory import StructuredJsonMemory
from themind.functions.send_message_function import SendMessageFunction
from themind.functions.fetch_memory_function import FetchMemoryFunction
from themind.functions.update_memory_function import UpdateMemoryFunction


class Agent(object):

    def __init__(self):
        self.structured_memory = StructuredJsonMemory()
        self.llm = OpenAILLM()
        
        self.functions = [SendMessageFunction(), FetchMemoryFunction(), UpdateMemoryFunction()]
        
        self.functions_map = {}
        for function in self.functions:
            self.functions_map[function.name] = function

    def run(self, uid: str, thread: Thread):
        
        # TODO: update here the thread
        
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
                thread.add_message(message=Message.assistent_message(content=response))
        
        return response

    def think_next_step(self, uid: str, thread: Thread) -> List[Function]:
        
        memory_schema = self.structured_memory.schema(uid=uid)
        
        prompt = """This is a schema representation of my structured memory: {memory_schema}
        """.format(memory_schema=memory_schema)
        
        thread.add_message(message=Message.assistent_message(content=prompt))
        
        # TODO: impl to_openai_messages
        messages = thread.to_openai_messages()
        function_calls, response_text = self.llm.choose_function_call(messages=messages, functions=self.functions)
        
        print(function_calls)
        print(response_text)
        
        return function_calls, response_text


if __name__ == '__main__':
    
    agent = Agent()
    
    uid = 'test'
    message = 'what exams do i have tomrrow? I like skateboarding.'
    
    thread = Thread.new_with_system_prompt(uid)
    
    new_message = Message.user_message(content=message)
    thread.add_message(message=new_message)
    
    agent.run(uid=uid, thread=thread)