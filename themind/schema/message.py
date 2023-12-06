from enum import Enum
from typing import List
from datetime import datetime
from pydantic import BaseModel
from pydantic.fields import Field
from themind.schema.function import Function


class Role(Enum):
    ASSISTANT = "assistent"
    USER = "user"
    SYSTEM = "system"
    TOOL = "tool"


class Message(BaseModel):
    
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
    content: str
    role: Role
    
    functions: List[Function] = []
    
    is_visible: bool = True
    
    @classmethod
    def user_message(cls, content: str):
        return cls(content=content, role=Role.USER, is_visible=True)
    
    @classmethod
    def function_message(cls, content: str, functions: List[Function]):
        return cls(content=content, role=Role.TOOL, functions=functions, is_visible=False)
    
    @classmethod
    def assistent_message(cls, content: str):
        return cls(content=content, role=Role.ASSISTANT, is_visible=True)