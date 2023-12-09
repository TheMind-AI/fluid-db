from pydantic import BaseModel
from typing import Optional


class User(BaseModel):
    uid: Optional[str] = None
    
    name: str
    email: str
