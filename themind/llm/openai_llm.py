import os
import json
import openai
import backoff
import instructor
from enum import Enum
from openai import OpenAI
from dotenv import load_dotenv
from themind.prompts.system_prompt import SystemPrompt


instructor.patch()


class OpenAIModel(Enum): 
    GPT3_TURBO = 'gpt-3.5-turbo-1106'
    GPT4_TURBO = 'gpt-4-1106-preview'
    

class OpenAILLM(object):

    def __init__(self):
        
        load_dotenv()
        
        self.client = OpenAI(
            api_key=os.environ.get("OPENAI_API_KEY"),
        )

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
        response = self.client.chat.completions.create(
            model=model,
            temperature=temperature,
            messages=[
                {"role": "system", "content": SystemPrompt.default},
                {"role": "user", "content": prompt}
            ],
            response_model=response_model
        )
        return response.choices[0].message.content

    def get_embedding(self, text):
        response = self.client.embeddings.create(
            model="text-embedding-ada-002",
            input=text
        )
        return response.data[0].embedding



