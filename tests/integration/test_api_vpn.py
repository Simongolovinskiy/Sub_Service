import asyncio
from datetime import datetime, timedelta
from uuid import uuid4

import asyncpg
import httpx
import pytest
from dishka import Provider, Scope, provide

from app.application import factory
from app.domain.ports.vpn_cache import VpnCachePort
from app.domain.ports.vpn_panel import VpnPanelPort
from app.infrastructure.adapters.di.main import PersistenceProvider
from tests.integration import test_vpn_cache, test_vpn_core
from tests.integration.test_vpn_core import FakePanel

vpn_database = test_vpn_core.vpn_database
redis_url = test_vpn_cache.redis_url


@pytest.fixture
async def api(monkeypatch, vpn_database, redis_url):
    from dishka import make_async_container

    from app.infrastructure.adapters.di.vpn import VpnProvider
    from app.infrastructure.adapters.vpn.settings import VpnRuntimeSettings

    conn, pool = vpn_database
    panel = FakePanel()

    class Overrides(Provider):
        @provide(scope=Scope.APP, override=True)
        def settings(self) -> VpnRuntimeSettings:
            return VpnRuntimeSettings(redis_url=redis_url, operation_timeout=0.08, retry_initial=0.01)

        @provide(scope=Scope.APP)
        def pool(self) -> asyncpg.Pool:
            return pool

        @provide(scope=Scope.APP, override=True)
        def panel(self) -> VpnPanelPort:
            return panel

    container = make_async_container(PersistenceProvider(), VpnProvider(), Overrides())
    monkeypatch.setattr(factory, 'build_container', lambda: container)
    app = factory.create_app()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            yield client, app, container, panel, conn


async def register(client, email='user@example.com'):
    response = await client.post('/users', json={'email': email, 'name': ' User '})
    assert response.status_code == 201, response.text
    assert set(response.json()) == {'id', 'registered_at'}
    assert response.json()['registered_at'].endswith('Z')
    return response.json()['id']


async def test_root_vpn_roundtrip_replays_and_filters(api):
    client, app, container, panel, conn = api
    user = await register(client, ' User@Example.COM ')
    duplicate = await client.post('/users', json={'email': 'user@example.com', 'name': 'Other'})
    assert duplicate.status_code == 409
    assert duplicate.json()['code'] == 'USER_ALREADY_EXISTS'
    assert (await client.get(f'/users/{user}/subscription')).status_code == 404
    created = await client.post(f'/users/{user}/subscription', headers={'Idempotency-Key': 'create'})
    assert created.status_code == 201, created.text
    assert set(created.json()) == {'subscription_url', 'expires_at', 'status'}
    assert created.json()['expires_at'].endswith('Z')
    extended = await client.post(f'/users/{user}/subscription/extend', headers={'Idempotency-Key': 'renew'})
    assert extended.status_code == 200
    assert datetime.fromisoformat(extended.json()['expires_at']) - datetime.fromisoformat(created.json()['expires_at']) == timedelta(days=7)
    replay = await client.post(f'/users/{user}/subscription', headers={'Idempotency-Key': 'create'})
    assert replay.json() == created.json()
    assert (await client.get(f'/users/{user}/subscription')).json() == extended.json()
    conflict = await client.post(f'/users/{user}/subscription/extend', headers={'Idempotency-Key': 'create'})
    assert conflict.status_code == 409
    another = await register(client, 'another@example.com')
    assert (await client.post(f'/users/{another}/subscription', headers={'Idempotency-Key': 'create'})).status_code == 409
    assert (await client.post(f'/users/{uuid4()}/subscription', headers={'Idempotency-Key': 'create'})).status_code == 404
    schema = app.openapi()
    assert schema['paths']['/users']['post']['requestBody']
    assert schema['paths']['/users/{user_id}/subscription']['post']['responses']['500']['content']['application/json']['schema']
    for path, method in [('/users', 'post'), ('/users/{user_id}/subscription', 'post'),
                         ('/users/{user_id}/subscription', 'get'), ('/users/{user_id}/subscription/extend', 'post')]:
        unavailable = schema['paths'][path][method]['responses']['503']['content']['application/json']
        assert unavailable['schema'] == {'$ref': '#/components/schemas/ErrorResponse'}
        assert unavailable['example'] == {'code': 'SERVICE_STOPPING', 'message': 'Service is shutting down'}
    assert (await client.get('/api/v1/health')).status_code == 404


async def test_cached_absence_never_resolves_pool(api, monkeypatch):
    client, app, container, panel, conn = api
    user = uuid4()
    cache = await container.get(VpnCachePort)
    generation, missing = await cache.probe(user)
    await cache.remember_missing(user, generation)
    original = type(container).get

    async def get(self, kind, *args, **kwargs):
        if kind is asyncpg.Pool:
            pytest.fail('cached404 resolved the database pool')
        return await original(self, kind, *args, **kwargs)

    monkeypatch.setattr(type(container), 'get', get)
    assert (await client.get(f'/users/{user}/subscription')).status_code == 404


@pytest.mark.parametrize('payload', [
    {'email': 'bad', 'name': 'User'}, {'email': 'a@b.test', 'name': '   '},
    {'email': 'a b@c.test', 'name': 'User'}, {'email': 'secret-value', 'name': []},
])
async def test_validation_uses_safe_error_envelope(api, payload):
    client, *_ = api
    response = await client.post('/users', json=payload)
    assert response.status_code == 422
    assert response.json() == {'code': 'VALIDATION_ERROR', 'message': 'Request validation failed'}


@pytest.mark.parametrize('key', [None, '', '   ', 'x' * 129])
async def test_required_idempotency_header(api, key):
    client, *_ = api
    headers = {} if key is None else {'Idempotency-Key': key}
    response = await client.post(f'/users/{uuid4()}/subscription', headers=headers)
    assert response.status_code == 422
    assert response.json()['code'] == 'VALIDATION_ERROR'


async def test_pending_external_error_is_durable_and_recovers(api):
    from app.services.vpn_outbox import VpnOutboxWorker

    client, app, container, panel, conn = api
    user = await register(client)
    panel.fail = True
    failed = await client.post(f'/users/{user}/subscription', headers={'Idempotency-Key': 'outage'})
    assert failed.status_code == 500
    assert failed.json() == {'code': 'INTEGRATION_UNAVAILABLE', 'message': 'External service unavailable; retry later'}
    assert await conn.fetchval("SELECT count(*) FROM outbox_events WHERE action = 'create' AND published_at IS NULL") == 1
    assert (await client.get(f'/users/{user}/subscription')).status_code == 500
    panel.fail = False
    await conn.execute('UPDATE outbox_events SET available_at = now()')
    await (await container.get(VpnOutboxWorker)).drain(asyncio.Event())
    replay = await client.post(f'/users/{user}/subscription', headers={'Idempotency-Key': 'outage'})
    assert replay.status_code == 201
    assert replay.json()['status'] == 'active'


@pytest.mark.parametrize('body', [b'not-json', b'\xff\xfe'])
async def test_malformed_body_is_safe(api, body):
    client, *_ = api
    response = await client.post('/users', content=body, headers={'Content-Type': 'application/json'})
    assert response.status_code in (400, 422)
    assert response.json() == {'code': 'VALIDATION_ERROR', 'message': 'Request validation failed'}


async def test_strict_cache_invalidation_error_remains_durable(api, monkeypatch):
    from app.domain.errors.vpn_user import CacheUnavailableError
    from app.infrastructure.adapters.cache.vpn import RedisVpnCache

    client, app, container, panel, conn = api
    user = await register(client)

    async def unavailable(self, user_guid):
        raise CacheUnavailableError()

    monkeypatch.setattr(RedisVpnCache, 'invalidate', unavailable)
    response = await client.post(f'/users/{user}/subscription', headers={'Idempotency-Key': 'cache-down'})
    assert response.status_code == 500
    assert response.json()['code'] == 'CACHE_UNAVAILABLE'
    assert panel.calls == []
    assert await conn.fetchval("SELECT count(*) FROM outbox_events WHERE action='create' AND published_at IS NULL") == 1


async def test_unexpected_error_never_escapes_or_exposes_secret(api, monkeypatch):
    from app.services.use_cases.users.register.handler import RegisterUserHandler

    async def fail(self, request):
        raise RuntimeError('postgresql://private:secret@database supplied-jwt')

    monkeypatch.setattr(RegisterUserHandler, 'handle', fail)
    client, *_ = api
    response = await client.post('/users', json={'email': 'user@example.com', 'name': 'User'})
    assert response.status_code == 500
    assert response.json() == {'code': 'INTERNAL_ERROR', 'message': 'An unexpected error occurred'}
