from unittest.mock import AsyncMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.application import factory
from app.infrastructure.adapters.vpn.settings import VpnRuntimeSettings


async def test_lifespan_closes_resources_on_error():
    container = AsyncMock()
    container.get.return_value = VpnRuntimeSettings()
    app = factory.create_app()
    app.state.container = container
    try:
        with pytest.raises(RuntimeError, match='request failed'):
            async with factory.lifespan(app):
                raise RuntimeError('request failed')
        container.close.assert_awaited_once()
    finally:
        await app.state.dishka_container.close()


async def test_only_vpn_routes_and_shared_health_metrics_are_exposed():
    app = factory.create_app()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            response = await client.get('/healthz?probe=liveness', headers={'x-request-id': 'fixture'})
            assert response.status_code == 200
            assert response.headers['x-request-id'] == 'fixture'
            response = await client.get('/metrics')
            assert response.status_code == 200
            assert 'http_requests_total' in response.text
            assert (await client.get('/api/v1/health')).status_code == 404
            assert (await client.get('/api/v1/products')).status_code == 404
    finally:
        await app.state.container.close()


async def test_global_validation_and_unexpected_errors_are_safe():
    app = factory.create_app()

    @app.get('/test-error')
    async def fail():
        raise RuntimeError('private credential')

    try:
        async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url='http://test') as client:
            invalid = await client.get('/healthz?probe=invalid')
            assert invalid.status_code == 422
            assert invalid.json() == {'code': 'VALIDATION_ERROR', 'message': 'Request validation failed'}
            failed = await client.get('/test-error')
            assert failed.status_code == 500
            assert failed.json() == {'code': 'INTERNAL_ERROR', 'message': 'An unexpected error occurred'}
            assert 'private' not in failed.text
    finally:
        await app.state.container.close()
