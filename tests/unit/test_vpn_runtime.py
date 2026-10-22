import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import asyncpg
import httpx
import pytest

from app.application import factory


async def test_four_use_case_handlers_resolve_without_database(monkeypatch):
    from dishka import AsyncContainer

    from app.domain.ports.uow import UnitOfWorkPort
    from app.infrastructure.adapters.di.main import build_container
    from app.services.use_cases.subscriptions.create.handler import CreateSubscriptionHandler
    from app.services.use_cases.subscriptions.extend.handler import ExtendSubscriptionHandler
    from app.services.use_cases.subscriptions.get.handler import GetSubscriptionHandler
    from app.services.use_cases.users.register.handler import RegisterUserHandler

    original = AsyncContainer.get

    async def get(self, kind, *args, **kwargs):
        assert kind not in (UnitOfWorkPort, asyncpg.Pool)
        return await original(self, kind, *args, **kwargs)

    monkeypatch.setattr(AsyncContainer, 'get', get)
    container = build_container()
    try:
        for handler_type in (RegisterUserHandler, CreateSubscriptionHandler, GetSubscriptionHandler, ExtendSubscriptionHandler):
            assert isinstance(await container.get(handler_type), handler_type)
    finally:
        await container.close()


@pytest.mark.parametrize('stopping', [False, True])
async def test_root_liveness_without_dependencies(monkeypatch, stopping):
    from dishka import make_async_container

    container = make_async_container()
    monkeypatch.setattr(factory, 'build_container', lambda: container)
    app = factory.create_app()
    if stopping:
        app.state.runtime.request_stop()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.get('/healthz?probe=liveness')
    assert response.status_code == 200
    assert response.json() == {'status': 'alive'}
    assert response.headers['cache-control'] == 'no-store'


@pytest.mark.parametrize(('method', 'path'), [
    ('POST', '/users'),
    ('POST', '/users/00000000-0000-0000-0000-000000000001/subscription'),
    ('POST', '/users/00000000-0000-0000-0000-000000000001/subscription/extend'),
    ('GET', '/users/00000000-0000-0000-0000-000000000001/subscription'),
])
async def test_stopping_rejects_business_requests_before_dependency_acquisition(monkeypatch, method, path):
    from dishka import AsyncContainer, Provider, Scope, make_async_container, provide

    from app.services.use_cases.subscriptions.create.handler import CreateSubscriptionHandler
    from app.services.use_cases.subscriptions.extend.handler import ExtendSubscriptionHandler
    from app.services.use_cases.subscriptions.get.handler import GetSubscriptionHandler
    from app.services.use_cases.users.register.handler import RegisterUserHandler

    register = Mock(spec=RegisterUserHandler)
    create = Mock(spec=CreateSubscriptionHandler)
    get = Mock(spec=GetSubscriptionHandler)
    extend = Mock(spec=ExtendSubscriptionHandler)
    acquired = Mock()

    class Services(Provider):
        @provide(scope=Scope.REQUEST)
        def register_user(self) -> RegisterUserHandler:
            acquired()
            return register

        @provide(scope=Scope.REQUEST)
        def create_subscription(self) -> CreateSubscriptionHandler:
            acquired()
            return create

        @provide(scope=Scope.REQUEST)
        def get_subscription(self) -> GetSubscriptionHandler:
            acquired()
            return get

        @provide(scope=Scope.REQUEST)
        def extend_subscription(self) -> ExtendSubscriptionHandler:
            acquired()
            return extend

    container = make_async_container(Services())
    monkeypatch.setattr(factory, 'build_container', lambda: container)
    app = factory.create_app()
    enter_scope = AsyncContainer.__call__
    entered = Mock()

    def counted_scope(self, *args, **kwargs):
        entered()
        return enter_scope(self, *args, **kwargs)

    monkeypatch.setattr(AsyncContainer, '__call__', counted_scope)
    app.state.runtime.request_stop()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.request(method, path, json={'email': 'user@example.com', 'name': 'User'},
                                        headers={'Idempotency-Key': 'new-operation'})
    assert response.status_code == 503
    assert response.json() == {'code': 'SERVICE_STOPPING', 'message': 'Service is shutting down'}
    entered.assert_not_called()
    acquired.assert_not_called()
    for handler in (register, create, get, extend):
        handler.handle.assert_not_awaited()
    await container.close()


@pytest.mark.parametrize('scope_type', ['lifespan', 'websocket'])
async def test_stopping_forwards_non_http_scopes(scope_type):
    from app.application.runtime import VpnRuntime, WorkTrackingMiddleware

    runtime = VpnRuntime()
    runtime.request_stop()
    downstream = AsyncMock()
    scope, receive, send = {'type': scope_type}, AsyncMock(), AsyncMock()
    await WorkTrackingMiddleware(downstream, runtime)(scope, receive, send)
    downstream.assert_awaited_once_with(scope, receive, send)


async def test_root_registration_route_exists(monkeypatch):
    from dishka import make_async_container

    monkeypatch.setattr(factory, 'build_container', make_async_container)
    app = factory.create_app()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.post('/users', json={'email': 'bad', 'name': ''})
    assert response.status_code == 422
    assert response.json() == {'code': 'VALIDATION_ERROR', 'message': 'Request validation failed'}
    await app.state.container.close()


@pytest.mark.parametrize('mode', ['resolve', 'acquire', 'query', 'failure', 'ready', 'draining'])
async def test_readiness_bounds_whole_database_check(monkeypatch, mode):
    from contextlib import asynccontextmanager


    app = factory.create_app()
    await app.state.container.close()
    app.state.runtime.initialized = True
    if mode == 'draining':
        app.state.runtime.stopping.set()

    class Pool:
        @asynccontextmanager
        async def acquire(self):
            if mode == 'acquire':
                await asyncio.sleep(10)
            connection = SimpleNamespace(fetchval=AsyncMock())
            if mode == 'query':
                async def slow_query(_):
                    await asyncio.sleep(10)
                connection.fetchval.side_effect = slow_query
            yield connection

    async def get(kind):
        assert kind is asyncpg.Pool
        if mode == 'resolve':
            await asyncio.sleep(10)
        if mode == 'failure':
            raise RuntimeError('postgresql://private:secret@database')
        if mode == 'draining':
            pytest.fail('draining readiness resolved database')
        return Pool()

    app.state.container = SimpleNamespace(get=get)
    started = asyncio.get_running_loop().time()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        response = await client.get('/healthz')
    elapsed = asyncio.get_running_loop().time() - started
    assert response.status_code == (200 if mode == 'ready' else 503)
    assert response.json() == {'status': 'ready' if mode == 'ready' else 'not_ready'}
    assert response.headers['cache-control'] == 'no-store'
    assert elapsed < 1.3


async def test_shutdown_awaits_active_http_and_scheduler_work(monkeypatch):
    from app.application.runtime import VpnRuntime, WorkTrackingMiddleware
    from app.infrastructure.adapters.di.main import build_container

    events = []
    container = build_container()
    runtime = VpnRuntime()
    await runtime.start(container)
    gate = asyncio.Event()
    job_started = asyncio.Event()
    started = asyncio.Event()
    cleanup = asyncio.Event()

    async def work(scope, receive, send):
        started.set()
        try:
            await gate.wait()
        finally:
            await cleanup.wait()
            events.append('http cleaned')

    middleware = WorkTrackingMiddleware(work, runtime)
    request = asyncio.create_task(middleware({'type': 'http'}, AsyncMock(), AsyncMock()))
    await started.wait()

    class Worker:
        async def drain(self, stop):
            events.append('drain')
            job_started.set()
            await gate.wait()
            events.append('job cleaned')

        async def reconcile(self, stop):
            events.append('reconcile')

    runtime.worker = Worker()
    runtime.scheduler.add_job(runtime.tick, 'date')
    await job_started.wait()
    job = next(iter(runtime.job_tasks))
    request.cancel()
    shutdown = asyncio.create_task(runtime.close())
    await asyncio.sleep(0.02)
    assert not shutdown.done()
    assert runtime.stopping.is_set()
    gate.set()
    await job
    assert not shutdown.done()
    cleanup.set()
    await shutdown
    await asyncio.gather(request, return_exceptions=True)
    assert events == ['drain', 'job cleaned', 'reconcile', 'http cleaned']
    assert not runtime.scheduler.running
    await container.close()


async def test_queued_tick_does_not_begin_after_stop():
    from app.application.runtime import VpnRuntime

    runtime = VpnRuntime()
    runtime.worker = SimpleNamespace(drain=AsyncMock(side_effect=AssertionError('late work')))
    task = asyncio.create_task(runtime.tick())
    runtime.stopping.set()
    await task


def test_settings_reject_invalid_timing_and_hide_secrets(monkeypatch):
    from pydantic import ValidationError

    from app.infrastructure.adapters.vpn.settings import VpnRuntimeSettings

    monkeypatch.setenv('VPN_PANEL_TOKEN', 'private-panel-token')
    monkeypatch.setenv('VPN_OPERATION_TIMEOUT', '0')
    with pytest.raises(ValidationError) as error:
        VpnRuntimeSettings(_env_file=None)
    assert 'private-panel-token' not in str(error.value)
    monkeypatch.setenv('VPN_OPERATION_TIMEOUT', '2')
    settings = VpnRuntimeSettings(_env_file=None)
    assert 'private-panel-token' not in repr(settings)
    assert settings.domain_settings().operation_timeout == 2


async def test_startup_failure_closes_container_and_logging(monkeypatch):
    events = []
    app = factory.create_app()
    await app.state.container.close()

    async def close():
        events.append('container')

    async def complete():
        events.append('logging')

    app.state.container = SimpleNamespace(get=AsyncMock(side_effect=RuntimeError('startup failed')), close=close)
    monkeypatch.setattr(factory, 'logger', SimpleNamespace(info=Mock(), complete=complete))
    with pytest.raises(RuntimeError, match='startup failed'):
        async with factory.lifespan(app):
            pytest.fail('startup should fail')
    assert events == ['container', 'logging']
    assert not app.state.runtime.scheduler.running


def test_launcher_configures_float_http_budget(monkeypatch):
    import uvicorn

    from app.__main__ import main

    configs = []
    monkeypatch.setenv('APP_WORKERS', '4')
    monkeypatch.setenv('APP_PORT', '8000')
    monkeypatch.setattr(uvicorn, 'run', lambda app, **kwargs: configs.append(uvicorn.Config(app, **kwargs)))
    main()
    assert configs[0].timeout_graceful_shutdown == 0.5
    assert configs[0].workers == 4
    assert configs[0].factory is True
