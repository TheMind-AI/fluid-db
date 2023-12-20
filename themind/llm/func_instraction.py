from inspect import signature
from pydantic.main import create_model
from themind.llm.openai_llm import OpenAILLM
from pydantic import BaseModel


def instruct(func):
    
    def wrapper(*args, **kwargs):

        # Get the docstring from the function
        docstring = func.__doc__

        # Get the function signature
        sig = signature(func)

        # Format the docstring with the arguments
        bound_args = sig.bind(*args, **kwargs)
        bound_args.apply_defaults()
        formatted_docstring = docstring.format_map(bound_args.arguments)

        # Create a Pydantic model from the return annotation
        return_annotation = sig.return_annotation

        if isinstance(return_annotation, type) and issubclass(return_annotation, BaseModel):
            ResponseModel = sig.return_annotation
        else:
            ResponseModel = create_model('ResponseModel', result=(return_annotation, ...))
        
        # run the OI call with reponse model
        openai_llm = OpenAILLM()
        result = openai_llm.instruction_instructor(formatted_docstring, response_model=ResponseModel)

        # return the result if return value if raw type
        if result.__class__.__name__ == 'ResponseModel':
            return result.result

        return result

    return wrapper