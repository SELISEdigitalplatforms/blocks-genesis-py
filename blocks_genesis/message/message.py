from dataclasses import dataclass

@dataclass
class Message:
    body: str
    type: str
