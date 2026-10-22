from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Protocol

from app.domain.ports.repositories.outbox import OutboxRepositoryPort
from app.domain.ports.repositories.vpn_subscription import VpnSubscriptionRepositoryPort
from app.domain.ports.repositories.vpn_user import VpnUserRepositoryPort


class UnitOfWorkPort(Protocol):
    vpn_users: VpnUserRepositoryPort
    vpn_subscriptions: VpnSubscriptionRepositoryPort
    outbox: OutboxRepositoryPort

    async def __aenter__(self) -> "UnitOfWorkPort":
        raise NotImplementedError

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        raise NotImplementedError

    async def commit(self) -> None:
        raise NotImplementedError

    async def rollback(self) -> None:
        raise NotImplementedError

    @property
    def in_transaction(self) -> bool:
        raise NotImplementedError


UowFactory = Callable[[], AbstractAsyncContextManager[UnitOfWorkPort]]
