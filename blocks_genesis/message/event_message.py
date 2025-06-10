from dataclasses import dataclass

@dataclass
class EventMessage:
    body: str
    type: str
