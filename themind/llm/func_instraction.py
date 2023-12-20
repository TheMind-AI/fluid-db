from themind.llm.openai_llm import OpenAILLM
from inspect import signature

def instruct(allow_thinking: bool = False):
    
    def decorator(func):

        def wrapper(*args, **kwargs):
            # Get the docstring from the function
            docstring = func.__doc__

            # Get the function signature
            sig = signature(func)

            # Bind the function arguments to their values
            bound_args = sig.bind(*args, **kwargs)
            bound_args.apply_defaults()

            # Format the docstring with the arguments
            formatted_docstring = docstring.format_map(bound_args.arguments)

            # Create an instance of OpenAILLM
            openai_llm = OpenAILLM()

            # Use the formatted docstring as the prompt for the instruction method
            result = openai_llm.instruction(formatted_docstring)

            return result

        return wrapper
    
    return decorator