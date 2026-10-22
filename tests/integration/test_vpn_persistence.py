from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest
from dishka import Provider, Scope, make_async_container, provide

from app.domain.dto.vpn_user import RegisteredVpnUser, VpnOutboxEvent, VpnSubscription
from app.domain.ports.repositories.outbox import OutboxRepositoryPort
from app.domain.ports.repositories.vpn_subscription import VpnSubscriptionRepositoryPort
from app.domain.ports.repositories.vpn_user import VpnUserRepositoryPort
from app.domain.ports.uow import UnitOfWorkPort
from app.infrastructure.adapters.di.main import PersistenceProvider


async def test_di_shared_transaction_commit_and_rollback(database_url):
    schema = 'test_uow_' + uuid4().hex
    conn = await asyncpg.connect(database_url)
    await conn.execute(f'CREATE SCHEMA "{schema}"')
    await conn.execute(f'SET search_path TO "{schema}"')
    await conn.execute(Path('migrations/000004_sub_service.sql').read_text())
    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=2, server_settings={'search_path': schema})

    class TestPoolProvider(Provider):
        @provide(scope=Scope.APP)
        def get_pool(self) -> asyncpg.Pool:
            return pool

    container = make_async_container(TestPoolProvider(), PersistenceProvider())
    try:
        async with container() as request:
            uow = await request.get(UnitOfWorkPort)
            users = await request.get(VpnUserRepositoryPort)
            subscriptions = await request.get(VpnSubscriptionRepositoryPort)
            outbox = await request.get(OutboxRepositoryPort)
            assert users is uow.vpn_users
            assert subscriptions is uow.vpn_subscriptions
            assert outbox is uow.outbox
            assert users._conn is subscriptions._conn is outbox._conn is uow._conn
            committed = RegisteredVpnUser(email='committed@example.com', name='Committed')
            async with uow:
                await users.add(committed)
            assert await users.get(committed.guid) == committed
            rolled_back = RegisteredVpnUser(email='rollback@example.com', name='Rollback')
            subscription = VpnSubscription(user_guid=rolled_back.guid)
            event = VpnOutboxEvent(user_guid=rolled_back.guid, action='create', version=1,
                                   request_id='rollback-key', payload=subscription)
            with pytest.raises(RuntimeError, match='rollback'):
                async with uow:
                    await users.add(rolled_back)
                    await subscriptions.add(subscription)
                    await outbox.add(event)
                    raise RuntimeError('rollback')
            assert await users.get(rolled_back.guid) is None
            assert await subscriptions.get(rolled_back.guid) is None
            assert await outbox.get_request('rollback-key') is None
    finally:
        await container.close()
        await pool.close()
        await conn.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await conn.close()
