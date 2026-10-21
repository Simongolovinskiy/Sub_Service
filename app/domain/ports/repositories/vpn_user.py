from typing import Protocol
from uuid import UUID

from app.domain.dto.vpn_user import RegisteredVpnUser


class VpnUserRepositoryPort(Protocol):
    async def add(self, user: RegisteredVpnUser) -> None: ...
    async def get(self, guid: UUID, *, lock: bool = False) -> RegisteredVpnUser | None: ...
