from pydantic import BaseModel


class Function(BaseModel):
    name: str
    agrs: dict