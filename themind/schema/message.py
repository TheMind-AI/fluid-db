from enum import Enum
from datetime import datetime
from pydantic import BaseModel
from pydantic.fields import Field


class Role(Enum):
    ASSISTANT = "assistent"
    USER = "user"
    SYSTEM = "system"


class Message(BaseModel):
    
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
    text: str
    role: Role