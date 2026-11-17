from dataclasses import dataclass

@dataclass
class StructuredReply:
    data: dict
    usage: dict | None = None
