from uuid import UUID

import asyncpg

from app.domain.dto.user import User
from app.domain.errors.user import UserAlreadyExistsError
from app.domain.ports.repositories.user import UserRepositoryPort


class RDBUserRepository(UserRepositoryPort):
    def __init__(self, conn: asyncpg.Connection):
        self._conn = conn

    async def add(self, user: User) -> None:
        try:
            await self._conn.execute(
                """
                INSERT INTO users
                    (guid, username, password, email, is_active, is_superadmin, created_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                """,
                user.guid, user.username, user.password, user.email,
                user.is_active, user.is_superadmin, user.created_at,
            )
        except asyncpg.UniqueViolationError:
            raise UserAlreadyExistsError() from None

    async def get_by_guid(self, guid: UUID) -> User | None:
        row = await self._conn.fetchrow(
            "SELECT guid, username, password, email, is_active, is_superadmin, created_at FROM users WHERE guid = $1",
            guid,
        )
        return User(**dict(row)) if row else None

    async def find_by_username(self, username: str) -> User | None:
        row = await self._conn.fetchrow(
            "SELECT guid, username, password, email, is_active, is_superadmin, created_at FROM users WHERE username = $1",
            username,
        )
        return User(**dict(row)) if row else None
