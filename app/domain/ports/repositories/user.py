from typing import Protocol
from uuid import UUID

from app.domain.dto.user import User


class UserRepositoryPort(Protocol):
    async def add(self, user: User) -> None: ...

    async def get_by_guid(self, guid: UUID) -> User | None: ...

    async def find_by_username(self, username: str) -> User | None: ...
