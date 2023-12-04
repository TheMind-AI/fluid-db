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
        
        # run f:think to get the next function to run
        
        # for all functions run it:
            # running functions async
    
         # how determine if done?
            
    def think(user_message: str, context: str) -> List[Function]:
        
        # reason about next step
            # call LLM
            # to pick the function with its params
        
        # return the function
