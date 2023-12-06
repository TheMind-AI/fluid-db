from typing import List
from datetime import datetime
from pydantic import BaseModel
from pydantic.fields import Field
from themind.schema.message import Message, Role
from themind.prompts.system_prompt import SystemPrompt


class Thread(BaseModel):
    
    uid: str
    
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    
    messages: List[Message]
    
    @classmethod
    def new_with_system_prompt(cls, uid: str):
        system_message = Message(content=SystemPrompt.default, role=Role.SYSTEM)
        return cls(uid=uid, messages=[system_message])

    
    def add_message(self, message: Message):
        self.messages.append(message)
        self.updated_at = datetime.utcnow()
        
    def to_openai_messages(self):
        return [{"role": message.role.value, "content": message.content} for message in self.messages if message.is_visible]
    