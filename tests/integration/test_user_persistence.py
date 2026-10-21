import asyncio
from datetime import datetime
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio
from dishka import Provider, Scope, make_async_container, provide
from yoyo import get_backend, read_migrations

from app.domain.dto.product import Product
from app.domain.dto.user import User
from app.domain.errors.user import UserAlreadyExistsError
from app.domain.ports.repositories.product import ProductRepositoryPort
from app.domain.ports.repositories.user import UserRepositoryPort
from app.infrastructure.adapters.di.main import PersistenceProvider
from app.infrastructure.adapters.persistence.rdb.repositories.user import (
    RDBUserRepository,
)
from app.infrastructure.ports.uow import UnitOfWorkPort

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def database(database_url):
    conn = await asyncpg.connect(database_url)
    schema = "test_users_" + uuid4().hex
    await conn.execute(f'CREATE SCHEMA "{schema}"')
    await conn.execute(f'SET search_path TO "{schema}"')
    await conn.execute(Path("migrations/000001_products.sql").read_text())
    await conn.execute(Path("migrations/000002_users.sql").read_text())
    try:
        yield conn, schema
    finally:
        await conn.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await conn.close()


async def test_repository_roundtrip_defaults_and_null_emails(database):
    conn, _ = database
    repo = RDBUserRepository(conn)
    user = User(username="alice", password="encoded")
    other = User(username="bob", password="encoded")
    assert user.guid != other.guid
    await repo.add(user)
    await repo.add(other)
    assert await repo.get_by_guid(user.guid) == user
    assert await repo.find_by_username("alice") == user
    assert await repo.get_by_guid(uuid4()) is None
    assert await repo.find_by_username("absent") is None


@pytest.mark.parametrize("duplicate", [
    {"username": "alice", "email": None},
    {"username": "bob", "email": "ALICE@example.com"},
])
async def test_duplicate_username_or_case_insensitive_email(database, duplicate):
    conn, _ = database
    repo = RDBUserRepository(conn)
    await repo.add(User(username="alice", password="encoded", email="alice@example.com"))
    with pytest.raises(UserAlreadyExistsError) as error:
        await repo.add(User(password="private", **duplicate))
    assert error.value.code == 409
    assert "private" not in str(error.value)


async def test_concurrent_duplicate_registration(database, database_url):
    _, schema = database
    async def add():
        conn = await asyncpg.connect(database_url, server_settings={"search_path": schema})
        try:
            await RDBUserRepository(conn).add(User(username="same", password="encoded"))
        finally:
            await conn.close()
    results = await asyncio.gather(add(), add(), return_exceptions=True)
    assert sum(result is None for result in results) == 1
    assert sum(isinstance(result, UserAlreadyExistsError) for result in results) == 1


async def test_di_shared_transaction_commit_and_rollback(database, database_url):
    _, schema = database
    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=2, server_settings={"search_path": schema})

    class TestPoolProvider(Provider):
        @provide(scope=Scope.APP)
        def get_pool(self) -> asyncpg.Pool:
            return pool

    container = make_async_container(TestPoolProvider(), PersistenceProvider())
    try:
        async with container() as request:
            uow = await request.get(UnitOfWorkPort)
            users = await request.get(UserRepositoryPort)
            products = await request.get(ProductRepositoryPort)
            assert users is uow.users
            assert products is uow.products
            assert users._conn is products._conn is uow._conn
            committed = User(username="committed", password="encoded")
            async with uow:
                await users.add(committed)
            assert await users.get_by_guid(committed.guid) == committed
            rolled_back = User(username="rolled-back", password="encoded")
            product = Product(name="item", slug="item", price_cents=10,
                              created_at=datetime(2026, 1, 1), updated_at=datetime(2026, 1, 1))
            with pytest.raises(RuntimeError):
                async with uow:
                    await users.add(rolled_back)
                    await products.add(product)
                    raise RuntimeError("rollback")
            assert await users.get_by_guid(rolled_back.guid) is None
            assert await products.get_by_guid(product.guid) is None
    finally:
        await container.close()
        await pool.close()


async def test_yoyo_apply_rollback_reapply(database, database_url):
    conn, schema = database
    await conn.execute("DROP TABLE users, products CASCADE")
    def migrate():
        url = urlsplit(database_url)
        query = urlencode(parse_qsl(url.query) + [("schema", schema)])
        backend = get_backend(urlunsplit(url._replace(query=query)))
        migrations = read_migrations("migrations")
        try:
            with backend.lock():
                backend.apply_migrations(backend.to_apply(migrations))
                backend.rollback_migrations(backend.to_rollback(migrations)[0:1])
                assert backend.execute("SELECT to_regclass('vpn_users')").fetchone()[0] is None
                assert backend.execute("SELECT to_regclass('vpn_subscriptions')").fetchone()[0] is None
                assert backend.execute("SELECT to_regclass('outbox_events')").fetchone()[0] is None
                assert backend.execute("SELECT to_regclass('users')").fetchone()[0] == "users"
                backend.apply_migrations(backend.to_apply(migrations))
        finally:
            backend.connection.close()
    await asyncio.to_thread(migrate)
    assert await conn.fetchval("SELECT to_regclass('users')") == "users"


async def test_registration_persists_only_hash(database):
    from app.infrastructure.adapters.persistence.rdb.repositories.product import (
        RDBProductRepository,
    )
    from app.infrastructure.adapters.persistence.rdb.uow import RDBUnitOfWork
    from app.infrastructure.adapters.security.password_hasher import (
        Argon2PasswordHasher,
    )
    from app.services.use_cases.users.add.handler import AddUserHandler
    from app.services.use_cases.users.add.request import AddUserRequest

    conn, _ = database
    users = RDBUserRepository(conn)
    from app.infrastructure.adapters.persistence.rdb.repositories.outbox import (
        RDBOutboxRepository,
    )
    from app.infrastructure.adapters.persistence.rdb.repositories.vpn_subscription import (
        RDBVpnSubscriptionRepository,
    )
    from app.infrastructure.adapters.persistence.rdb.repositories.vpn_user import (
        RDBVpnUserRepository,
    )
    uow = RDBUnitOfWork(conn, RDBProductRepository(conn), users, RDBVpnUserRepository(conn),
                        RDBVpnSubscriptionRepository(conn), RDBOutboxRepository(conn))
    hasher = Argon2PasswordHasher()
    response = await AddUserHandler(uow, hasher).handle(AddUserRequest("registered", "private"))
    user = await users.get_by_guid(response.guid)
    assert user.password != "private"
    assert await hasher.verify("private", user.password)
    assert user.email is None
    assert user.is_active and not user.is_superadmin


@pytest.mark.parametrize("field", ["username", "email"])
async def test_database_rejects_blank_identity_fields(database, field):
    conn, _ = database
    values = {"username": "alice", "password": "encoded", field: " "}
    with pytest.raises(asyncpg.CheckViolationError):
        await RDBUserRepository(conn).add(User(**values))
