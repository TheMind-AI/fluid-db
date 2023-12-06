from typing import List
from themind.schema.thread import Thread
from themind.schema.message import Message
from themind.llm.openai_llm import OpenAILLM
from themind.schema.function import Function
from themind.prompts.agent_prompt import AgentPrompt
from themind.memory.struct_memory import StructuredMemory


class Agent(object):

    def __init__(self):
        self.structed_memory = StructuredMemory()
        self.llm = OpenAILLM()
        
        # TODO: init functions
        self.functions = []
        
        self.functions_map = {}
        for function in self.available_functions:
            self.functions_map[function.name] = function

    def run(self, uid: str, user_message: Message, thread: Thread):
        
        # TODO: when to append to thread?
        
        function_calls = []
        while [call.name for call in function_calls] not in 'send_message':
            function_calls = self.think_next_step(thread=thread)
            for function_call in function_calls:
                reponse = self.functions_map[function_call.name].run(**function_call.args)
                # TODO: appended to thread as assistent msg
        
        return reponse
        
            
    def think_next_step(self, thread: Thread) -> List[Function]:
        
        # not needed for now?
        # prompt = AgentPrompt.v1.format(message=message, thread=thread)
        
        # TODO: append memory schema function calling decision.
        memory_schema = self.structed_memory.get_schema(uid=uid)
        
        # TODO: impl to_openai_messages
        messages = thread.to_openai_messages()
        function_calls, reponse_text = self.llm.choose_function_call(messages=[messages], functions=self.available_functions)
        
        print(function_calls)
        print(reponse_text)
        
        return function_calls


if __name__ == '__main__':
    
    agent = Agent()
    
    uid = 'test'
    thread = Thread.new_with_system_prompt(uid)
    new_message = Message.user_message(text='hello')
    
    agent.run(uid=uid, user_message=new_message, thread=thread)