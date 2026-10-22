import logging
from collections.abc import AsyncGenerator
from typing import Self

import asyncpg
import msgspec
from dishka import AsyncContainer, Provider, Scope, make_async_container, provide

from app.domain.common import constants
from app.domain.common.enums import SecretsEnum
from app.domain.core.config.provider import SourceProviderPort
from app.domain.ports.repositories.outbox import OutboxRepositoryPort
from app.domain.ports.repositories.vpn_subscription import VpnSubscriptionRepositoryPort
from app.domain.ports.repositories.vpn_user import VpnUserRepositoryPort
from app.domain.ports.uow import UnitOfWorkPort
from app.infrastructure.adapters.di.factory import provide_source_provider
from app.infrastructure.adapters.di.vpn import VpnProvider
from app.infrastructure.adapters.persistence.rdb.repositories.outbox import (
    RDBOutboxRepository,
)
from app.infrastructure.adapters.persistence.rdb.repositories.vpn_subscription import (
    RDBVpnSubscriptionRepository,
)
from app.infrastructure.adapters.persistence.rdb.repositories.vpn_user import (
    RDBVpnUserRepository,
)
from app.infrastructure.adapters.persistence.rdb.uow import RDBUnitOfWork

logger = logging.getLogger(__name__)


# ============================================================================
# CONFIGURATION
# ============================================================================

class DatabaseConfig(msgspec.Struct):
    connection_string: str

    @classmethod
    def load(cls, source_provider: SourceProviderPort) -> Self:
        return cls(
            connection_string=source_provider.get_variable(
                SecretsEnum.DATABASE_CONNECTION_STRING, str
            )
        )


class ConfigProvider(Provider):

    scope = Scope.APP

    @provide(scope=Scope.APP)
    async def get_source_provider(self) -> SourceProviderPort:
        return await provide_source_provider()

    @provide(scope=Scope.APP)
    def get_database_config(
        self, source_provider: SourceProviderPort
    ) -> DatabaseConfig:
        return DatabaseConfig.load(source_provider)

class PoolProvider(Provider):

    scope = Scope.APP

    @provide(scope=Scope.APP)
    async def get_pool(self, config: DatabaseConfig) -> AsyncGenerator[asyncpg.Pool, None]:
        pool = await asyncpg.create_pool(
            config.connection_string,
            min_size=constants.MIN_POOL_SIZE,
            max_size=constants.MAX_POOL_SIZE,
        )
        logger.info("Database pool ready")
        try:
            yield pool
        finally:
            await pool.close()
            logger.info("Database pool closed")


class PersistenceProvider(Provider):

    scope = Scope.REQUEST

    @provide(scope=Scope.REQUEST)
    async def get_connection(
        self, pool: asyncpg.Pool
    ) -> AsyncGenerator[asyncpg.Connection, None]:
        async with pool.acquire() as conn:
            yield conn

    @provide(scope=Scope.REQUEST)
    def get_vpn_user_repo(self, conn: asyncpg.Connection) -> VpnUserRepositoryPort:
        return RDBVpnUserRepository(conn)

    @provide(scope=Scope.REQUEST)
    def get_vpn_subscription_repo(self, conn: asyncpg.Connection) -> VpnSubscriptionRepositoryPort:
        return RDBVpnSubscriptionRepository(conn)

    @provide(scope=Scope.REQUEST)
    def get_outbox_repo(self, conn: asyncpg.Connection) -> OutboxRepositoryPort:
        return RDBOutboxRepository(conn)

    @provide(scope=Scope.REQUEST, provides=UnitOfWorkPort)
    async def get_uow(
        self,
        conn: asyncpg.Connection,
        vpn_user_repo: VpnUserRepositoryPort,
        vpn_subscription_repo: VpnSubscriptionRepositoryPort,
        outbox_repo: OutboxRepositoryPort,
    ) -> UnitOfWorkPort:
        uow = RDBUnitOfWork(
            conn=conn,
            vpn_users=vpn_user_repo,
            vpn_subscriptions=vpn_subscription_repo,
            outbox=outbox_repo,
        )
        return uow


# ============================================================================
# CONTAINER
# ============================================================================

def build_container() -> AsyncContainer:
    container = make_async_container(
        ConfigProvider(),
        PoolProvider(),
        PersistenceProvider(),
        VpnProvider(),
    )
    logger.info("DI container created")
    return container
