from pydantic import BaseModel


class EventMessage(BaseModel):
    body: str
    type: str
