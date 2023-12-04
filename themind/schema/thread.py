from datetime import datetime
from pydantic import BaseModel
from pydantic.fields import Field


class Thread(BaseModel):
    
    created_at: datetime = Field(default_factory=datetime.utcnow)