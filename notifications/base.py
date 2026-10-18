from typing import Protocol


class MessageSender(Protocol):
    def send(self, message: str) -> list[object]:
        ...
