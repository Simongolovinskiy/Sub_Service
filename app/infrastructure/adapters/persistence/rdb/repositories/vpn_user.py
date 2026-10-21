from uuid import UUID

import asyncpg

from app.domain.dto.vpn_user import RegisteredVpnUser
from app.domain.errors.vpn_user import VpnUserAlreadyExistsError
from app.domain.ports.repositories.vpn_user import VpnUserRepositoryPort


class RDBVpnUserRepository(VpnUserRepositoryPort):
    def __init__(self, conn: asyncpg.Connection):
        self._conn = conn

    async def add(self, user: RegisteredVpnUser) -> None:
        try:
            await self._conn.execute(
                'INSERT INTO vpn_users (guid,email,name,registered_at) VALUES ($1,$2,$3,$4)',
                user.guid, user.email, user.name, user.registered_at,
            )
        except asyncpg.UniqueViolationError:
            raise VpnUserAlreadyExistsError() from None

    async def get(self, guid: UUID, *, lock: bool = False) -> RegisteredVpnUser | None:
        row = await self._conn.fetchrow(
            'SELECT * FROM vpn_users WHERE guid=$1' + (' FOR UPDATE' if lock else ''), guid
        )
        return RegisteredVpnUser(**dict(row)) if row else None
