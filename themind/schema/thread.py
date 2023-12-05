from typing import List
from datetime import datetime
from pydantic import BaseModel
from pydantic.fields import Field
from themind.schema.message import Message


class Thread(BaseModel):
    
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
    messages: List[Message]