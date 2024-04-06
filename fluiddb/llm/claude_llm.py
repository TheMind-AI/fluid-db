import os
import json
import backoff
from dotenv import load_dotenv
from typing import List, Tuple, Callable
import anthropic
import instructor

class ClaudeModel:
    OPUS = 'claude-3-opus-20240229'
    SONNET = 'claude-3-sonnet-20240229'
    HAIKU = 'claude-3-haiku-20240307'

class ClaudeLLM(object):

    def __init__(self):
        
        load_dotenv()
        
        if os.environ.get("ANTHROPIC_API_KEY"):
            print("Anthropic API Key is set.")
        else:
            print("Anthropic API Key is not set.")
        
        self.client = anthropic.Anthropic(
            api_key=os.environ.get("ANTHROPIC_API_KEY"),
        )

    @backoff.on_exception(backoff.expo, anthropic.RateLimitError)
    def instructor(self, prompt, response_model, model: ClaudeModel = ClaudeModel.OPUS, temperature=0.4, max_tokens=2000):
        
        print(model)
        
        create = instructor.patch(
            create=anthropic.Anthropic().messages.create, mode=instructor.Mode.ANTHROPIC_TOOLS
        )

        print(create)
        
        response = create(
            model=model,
            system="You're a helpful assistant.",
            messages=[
                {"role": "user", "content": prompt}
            ],
            temperature=temperature,
            stream=False,
            max_tokens=max_tokens,
            response_model=response_model,
        )
        
        print(response)
        
        try:
            text = response.content[0].text
        except Exception as e:
            print(response)
            print(e)
        
        return text
    
    @backoff.on_exception(backoff.expo, anthropic.RateLimitError)
    def instruct(self, prompt, model: ClaudeModel = ClaudeModel.OPUS, temperature=0.4, max_tokens=2000):
        
        response = self.client.messages.create(
            model=model,
            system="You're a helpful assistant.",
            messages=[
                {"role": "user", "content": prompt}
            ],
            temperature=temperature,
            stream=False,
            max_tokens=max_tokens,
        )
        
        print(response)
        
        try:
            text = response.content[0].text
        except Exception as e:
            print(response)
            print(e)
        
        return text

    @backoff.on_exception(backoff.expo, anthropic.RateLimitError)
    def chat(self, system_prompt: str, messages: List[dict], model: ClaudeModel = ClaudeModel.OPUS, temperature=0.4, max_tokens=400, 
             stream=False, on_complete: Callable[[str], None] = lambda x: None):
        
        response = self.client.messages.create(
            model=model,
            system=system_prompt,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=stream
        )
        if stream:
            return self.process_completion_stream(response, on_complete=on_complete)

        return response.content[0].text
    
    def process_completion_stream(self, completion, on_complete: Callable[[str], None]):
        full_response = ""
        for response in completion:
            if hasattr(response, 'type'):
                if response.type == 'content_block_delta':
                    delta_text = response.delta.text if hasattr(response.delta, 'text') else ''
                    full_response += delta_text
                    yield delta_text
                # OTHER EVENTS THAT WE DONT NEED 
                # elif response.type == 'message_stop':
                #     print('DONE.')
                #     print(response)
                # elif response.type == 'message_start':
                #     print('START')
                #     print(response)
                # elif response.type == 'message_delta':
                #     print('DELTA')
                #     print(response)
                # elif response.type == 'content_block_start':
                #     print('START CONTENT')
                #     print(response)
                
        on_complete(full_response)


