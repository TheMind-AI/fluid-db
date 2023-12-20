from inspect import signature
from pydantic.main import create_model
from themind.llm.openai_llm import OpenAILLM


def instruct(func):
    
    def wrapper(*args, **kwargs):

        # Get the docstring from the function
        docstring = func.__doc__

        # Get the function signature
        sig = signature(func)

        # Create a Pydantic model from the return annotation
        return_annotation = sig.return_annotation
        ResponseModel = create_model('ResponseModel', result=(return_annotation, ...))
        
        # Bind the function arguments to their values
        bound_args = sig.bind(*args, **kwargs)
        bound_args.apply_defaults()

        # Format the docstring with the arguments
        formatted_docstring = docstring.format_map(bound_args.arguments)

        # run the OI call with reponse model
        openai_llm = OpenAILLM()
        result = openai_llm.instruction_instructor(formatted_docstring, response_model=ResponseModel)

        return result

    return wrapper