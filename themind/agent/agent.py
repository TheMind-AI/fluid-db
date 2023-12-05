from themind.memory.struct_memory import StructMemory
from themind.schema.thread import Thread
from themind.schema.message import Message


class Agent(object):

    def __init__(self):
        # init LLM
        # init StructMemory
        # init Available Tools
        
    def run(self, uid: str, user_message: Message, thread: Thread):
        
        # create context for agent
        # get schema from StructMemory
        # get past Thread messages
        
        # run f:think_next_step to get the next function to run
        # and run this until you get sent_message_to_user function which ends everything the agent run

            
    def think_next_step(user_message: str, context: str) -> List[Function]:
        
        # reason about next step
            # call LLM
            # to pick the function with its params
        
        # return the function


if __name__ == '__main__':
    
    agent = Agent()
    
    uid = 'test'
    thread = Thread.new_with_system_prompt(uid)
    new_message = Message.user_message(text='hello')
    
    agent.run(uid=uid, user_message=new_message, thread=thread)