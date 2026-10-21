import uuid
from unittest.mock import AsyncMock

import pytest
from dishka import Provider, Scope, make_async_container, provide
from httpx import ASGITransport, AsyncClient

from app.infrastructure.ports.uow import UnitOfWorkPort


@pytest.fixture
async def client(monkeypatch):
    from app.application import factory
    from app.infrastructure.adapters.di.vpn import VpnProvider

    products = {}
    uow = AsyncMock(spec=UnitOfWorkPort)
    uow.__aenter__.return_value = uow
    uow.products = AsyncMock()
    uow.products.add.side_effect = lambda product: products.update({product.guid: product})
    uow.products.get_by_guid.side_effect = products.get

    class TestProvider(Provider):
        @provide(scope=Scope.REQUEST)
        def get_uow(self) -> UnitOfWorkPort:
            return uow

    container = make_async_container(VpnProvider(), TestProvider())
    monkeypatch.setattr(factory, "build_container", lambda: container)
    app = factory.create_app()
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac


async def test_health_check(client):
    response = await client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


async def test_create_and_get_product(client):
    payload = {"name": "Test Product", "slug": "test-product", "price_cents": 9999,
               "description": "Test description"}
    response = await client.post("/api/v1/products", json=payload)
    assert response.status_code == 201
    created = response.json()
    assert {key: created[key] for key in payload} == payload
    assert created["created_at"] and created["updated_at"]
    fetched = await client.get(f"/api/v1/products/{created['guid']}")
    assert fetched.status_code == 200
    assert fetched.json() == created


async def test_get_product_not_found(client):
    response = await client.get(f"/api/v1/products/{uuid.uuid4()}")
    assert response.status_code == 404


async def test_invalid_request(client):
    response = await client.post("/api/v1/products", json={"name": "missing fields"})
    assert response.status_code == 422


async def test_lifespan_closes_resources_on_error(monkeypatch):
    from app.application import factory

    container = AsyncMock()
    from app.infrastructure.adapters.vpn.settings import VpnRuntimeSettings
    container.get.return_value = VpnRuntimeSettings()
    app = factory.create_app()
    app.state.container = container
    with pytest.raises(RuntimeError, match="request failed"):
        async with factory.lifespan(app):
            raise RuntimeError("request failed")
    container.close.assert_awaited_once()
    await app.state.dishka_container.close()


async def test_product_roundtrip_with_postgresql(database_url, monkeypatch):
    from pathlib import Path

    import asyncpg

    from app.application import factory
    from app.infrastructure.adapters.di.main import PersistenceProvider
    from app.infrastructure.adapters.di.vpn import VpnProvider

    schema = "test_products_" + uuid.uuid4().hex
    conn = await asyncpg.connect(database_url)
    await conn.execute(f'CREATE SCHEMA "{schema}"')
    await conn.execute(f'SET search_path TO "{schema}"')
    await conn.execute(Path("migrations/000001_products.sql").read_text())
    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=2,
                                    server_settings={"search_path": schema})

    class PoolProvider(Provider):
        @provide(scope=Scope.APP)
        def get_pool(self) -> asyncpg.Pool:
            return pool

    container = make_async_container(PoolProvider(), PersistenceProvider(), VpnProvider())
    monkeypatch.setattr(factory, "build_container", lambda: container)
    app = factory.create_app()
    try:
        async with app.router.lifespan_context(app):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                payload = {"name": "Persisted", "slug": "persisted", "price_cents": 10}
                created = await ac.post("/api/v1/products", json=payload)
                assert created.status_code == 201
                fetched = await ac.get(f"/api/v1/products/{created.json()['guid']}")
                assert fetched.status_code == 200
                assert fetched.json() == created.json()
                from app.infrastructure.adapters.persistence.rdb.repositories.product import (
                    RDBProductRepository,
                )

                repo = RDBProductRepository(conn)
                product = await repo.get_by_guid(uuid.UUID(created.json()["guid"]))
                await repo.update(product)
                assert await repo.get_by_guid(product.guid)
                assert [item async for item in repo.list_newer_than(product.created_at)] == []
                assert [item async for item in repo.list_older_than(product.created_at)] == []
    finally:
        await pool.close()
        await conn.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await conn.close()


@pytest.mark.parametrize("body", [b"not-json", b"\xff\xfe", b'{"password":"private"}'])
async def test_invalid_body_returns_safe_validation_error(client, body):
    response = await client.post("/api/v1/products", content=body,
                                 headers={"Content-Type": "text/plain"})
    assert response.status_code == 422
    details = response.json()["details"]
    assert details
    assert all(set(error) <= {"type", "loc", "msg"} for error in details)
    assert "private" not in response.text
