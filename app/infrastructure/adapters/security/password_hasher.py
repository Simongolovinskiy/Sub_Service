import asyncio

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.domain.ports.password_hasher import PasswordHasherPort


class Argon2PasswordHasher(PasswordHasherPort):
    def __init__(self):
        self._hasher = PasswordHasher()

    async def hash(self, password: str) -> str:
        return await asyncio.to_thread(self._hasher.hash, password)

    async def verify(self, password: str, encoded_hash: str) -> bool:
        try:
            return await asyncio.to_thread(self._hasher.verify, encoded_hash, password)
        except (InvalidHashError, VerifyMismatchError):
            return False
