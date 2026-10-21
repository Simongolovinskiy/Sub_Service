from __future__ import annotations

import logging
from typing import Any

import asyncpg
import asyncpg.transaction as tx

from app.domain.errors.adapters import UoWError
from app.domain.ports.repositories.outbox import OutboxRepositoryPort
from app.domain.ports.repositories.product import ProductRepositoryPort
from app.domain.ports.repositories.user import UserRepositoryPort
from app.domain.ports.repositories.vpn_subscription import VpnSubscriptionRepositoryPort
from app.domain.ports.repositories.vpn_user import VpnUserRepositoryPort
from app.infrastructure.ports.uow import UnitOfWorkPort

logger = logging.getLogger(__name__)


class RDBUnitOfWork(UnitOfWorkPort):

    def __init__(
        self,
        conn: asyncpg.Connection,
        products: ProductRepositoryPort,
        users: UserRepositoryPort,
        vpn_users: VpnUserRepositoryPort,
        vpn_subscriptions: VpnSubscriptionRepositoryPort,
        outbox: OutboxRepositoryPort,
    ):
        self._conn = conn
        self._tx: tx.Transaction | None = None
        self._in_transaction = False

        self.products = products
        self.users = users
        self.vpn_users = vpn_users
        self.vpn_subscriptions = vpn_subscriptions
        self.outbox = outbox

    @property
    def in_transaction(self) -> bool:
        return self._in_transaction

    async def __aenter__(self) -> RDBUnitOfWork:
        if self._in_transaction:
            logger.error("Attempted to start nested transaction")
            raise UoWError("Nested transactions are not allowed")

        try:
            logger.debug("Starting database transaction")
            self._tx = self._conn.transaction()
            await self._tx.start()
            self._in_transaction = True
            logger.debug("Transaction started successfully")
            return self
        except Exception as exc:
            logger.error("Failed to start transaction", exc_info=True)
            raise UoWError("Failed to start database transaction") from exc

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if not self._in_transaction:
            logger.warning("Transaction already closed (possible double exit)")
            return

        try:
            if exc_type is not None:
                logger.warning(f"Transaction failed with {exc_type.__name__}, rolling back...")
                await self.rollback()
            else:
                logger.debug("Transaction successful, committing...")
                await self.commit()
        except Exception as exc:
            logger.critical("Critical error during transaction cleanup", exc_info=True)
            raise UoWError("Failed to finalize transaction") from exc
        finally:
            self._in_transaction = False
            self._tx = None
            logger.debug("Transaction context exited")

    async def commit(self) -> None:
        if not self._in_transaction or not self._tx:
            logger.warning("Attempted to commit outside of active transaction")
            return

        try:
            logger.debug("Committing transaction...")
            await self._tx.commit()
            logger.info("Transaction committed successfully")
        except Exception as exc:
            logger.error("Failed to commit transaction", exc_info=True)
            raise UoWError("Transaction commit failed") from exc
        finally:
            self._in_transaction = False

    async def rollback(self) -> None:
        if not self._in_transaction or not self._tx:
            logger.warning("Attempted to rollback outside of active transaction")
            return

        try:
            logger.debug("Rolling back transaction...")
            await self._tx.rollback()
            logger.info("Transaction rolled back")
        except Exception as exc:
            logger.critical("Failed to rollback transaction!", exc_info=True)
            raise UoWError("Failed to rollback transaction") from exc
        finally:
            self._in_transaction = False
