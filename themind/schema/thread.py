from typing import List
from datetime import datetime
from pydantic import BaseModel
from pydantic.fields import Field
from themind.schema.message import Message, Role
from themind.schema.system_prompt import SystemPrompt


class Thread(BaseModel):
    
    uid: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    
    messages: List[Message]
    
    @classmethod
    def new_with_system_prompt(cls, uid: str):
        system_message = Message(text=SystemPrompt.default, role=Role.SYSTEM)
        return cls(uid=uid, messages=[system_message])
