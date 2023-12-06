from enum import Enum
from datetime import datetime
from pydantic import BaseModel
from pydantic.fields import Field


class Role(Enum):
    ASSISTANT = "assistent"
    USER = "user"
    SYSTEM = "system"
    TOOL = "tool"


class Message(BaseModel):
    
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
    text: str
    role: Role
    
    @classmethod
    def user_message(cls, message: str):
        return cls(text=message, role=Role.USER)