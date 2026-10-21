from typing import Protocol


class PasswordHasherPort(Protocol):
    async def hash(self, password: str) -> str: ...

    async def verify(self, password: str, encoded_hash: str) -> bool: ...
