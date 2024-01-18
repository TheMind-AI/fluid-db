
class AgentPrompt:
    
    v1 = """
    You have a new message from the user.
    
    Message: {message}
    Paset conversation: {thread}
    
    And pick one of the following functions to run:
    {functions}
    
    Breath, think step by step and then return the function.
    """