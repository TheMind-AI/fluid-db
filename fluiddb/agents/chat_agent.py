from themind.schema.thread import Thread
from themind.schema.message import Message
from themind.schema.location import Location
from themind.llm.openai_llm import OpenAILLM
from themind.agents.struct_memory_agent import StructMemoryAgent
from themind.services.thread_service import ThreadService


# DEPRECATED
class ChatAgent(object):

    def __init__(self, firebase_app):
        self.llm = OpenAILLM()

        self.struct_memory_agent = StructMemoryAgent()
        
        self.thread_service = ThreadService(firebase_app)
        
        # setup for function calling
        self.functions = [self.update_memory_function]
        self.functions_map = {}
        for function in self.functions:
            self.functions_map[function.name] = function

    def chat(self, uid: str, content: str, thread_id: str = None, location: Location = None):

        # get the thread from the database
        thread = self.thread_service.get_thread(uid=uid, thread_id=thread_id)
        # append the message to the thread
        thread.add_message(message=Message.user_message(content=content))

        # run rag on memory (we could use router which decides if to use strctrured memory or unstracted)

        # run maybe retrive from agent memory
        self.struct_memory_agent.retrieve_struct_memory(uid=uid, thread=thread)
    
        # ASYNC[run  maybe save to memory with retrived info]
        self.struct_memory_agent.update_struct_memory(uid=uid, thread=thread)
        
        self.thread_service.update_thread(uid=uid, thread=thread)

        def on_complete_closure(response):
            self.save_reponse(uid, thread_id, response)
            
        # call AI chat service with retrived memory
        response = self.llm.chat(messages=thread.to_openai_messages(), stream=True, on_complete=on_complete_closure)

        return response
    
    # TODO: move to a ThreadService
    def get_thread(self, uid: str, thread_id: str = None):
        if thread_id is None:
            thread = Thread.new_with_system_prompt(uid=uid)
        else:
            thread = self.thread_repo.get_thread(uid=uid, thread_id=thread_id)
            
        return thread
    
    def save_reponse(self, uid: str, thread_id: str, reply: str):
        thread = self.thread_service.get_thread(uid=uid, thread_id=thread_id)
        thread.add_message(message=Message.assistent_message(content=reply))
        self.thread_service.update_thread(uid=uid, thread=thread)


if __name__ == '__main__':

    from themind.firebase_app import firebase_app
    chat_agent = ChatAgent(firebase_app)

    uid = 'yP02mdoU5gV8Hj8TJjq1'
    content = 'ahoj my name is adam'

    response_stream, thread_id = chat_agent.chat(uid, content)

    print(thread_id)
    for message in response_stream:
        print(message, end='')
