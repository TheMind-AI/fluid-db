from pydantic import Field
from typing import Optional
from datetime import datetime
from pydantic import BaseModel


class User(BaseModel):
    uid: Optional[str] = None
    
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
    name: str
    email: str
