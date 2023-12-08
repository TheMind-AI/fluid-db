import os
import json
import openai
import backoff
import instructor
from enum import Enum
import openai
from openai import OpenAI
from dotenv import load_dotenv
from typing import List, Tuple
from themind.schema.function import Function
from themind.prompts.system_prompt import SystemPrompt
from themind.functions.function_base import FunctionBase


load_dotenv()


class OpenAIModel(Enum): 
    GPT3_TURBO = 'gpt-3.5-turbo-1106'
    GPT4_TURBO = 'gpt-4-1106-preview'
    

class OpenAILLM(object):

    def __init__(self):
        
        self.client = openai.OpenAI(
            api_key=os.environ.get("OPENAI_API_KEY"),
        )

        self.instructor_client = instructor.patch(self.client)

    @backoff.on_exception(backoff.expo, openai.RateLimitError)
    def instruction(self, prompt, model: OpenAIModel = OpenAIModel.GPT4_TURBO, temperature=0):
        print(model)
        response = self.client.chat.completions.create(
            model=model.value,
            temperature=temperature,
            messages=[
                {"role": "system", "content": SystemPrompt.default},
                {"role": "user", "content": prompt}
            ]
        )
        return response.choices[0].message.content

    @backoff.on_exception(backoff.expo, openai.RateLimitError)
    def instruction_instructor(self, prompt, response_model, model: OpenAIModel = OpenAIModel.GPT4_TURBO, temperature=0):
        response = self.instructor_client.chat.completions.create(
            model=model.value,
            temperature=temperature,
            messages=[
                {"role": "system", "content": SystemPrompt.default},
                {"role": "user", "content": prompt}
            ],
            response_model=response_model
        )
        return response
    
    @backoff.on_exception(backoff.expo, openai.RateLimitError)
    def choose_function_call(self, messages, functions: List[FunctionBase]) -> Tuple[List[Function], str]:
        
        openai_tools = []
        for function in functions:
            # TODO: implement openai_schema
            print(function)
            openai_tools.append(function.openai_schema())

        print(openai_tools)
        print(messages)

        response = self.client.chat.completions.create(
            model=OpenAIModel.GPT4_TURBO.value,
            messages=messages,
            tools=openai_tools,            
            tool_choice="auto",
        )

        print(response)

        response_message = response.choices[0].message
        tool_calls = response_message.tool_calls

        functions_to_call = []
        if tool_calls:
            for tool_call in tool_calls:
                f_name = tool_call.function.name
                f_args = json.loads(tool_call.function.arguments)
                function = Function(name=f_name, args=f_args)
                functions_to_call.append(function)

        return functions_to_call, response_message.content

    def get_embedding(self, text):
        response = self.client.embeddings.create(
            model="text-embedding-ada-002",
            input=text
        )
        return response.data[0].embedding



