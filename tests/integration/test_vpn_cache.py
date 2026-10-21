from uuid import uuid4

import pytest
import pytest_asyncio
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import LogMessageWaitStrategy

from app.domain.dto.vpn_user import VpnSettings, VpnSubscription
from app.domain.errors.vpn_user import CacheUnavailableError
from tests.integration.test_vpn_core import core as core
from tests.integration.test_vpn_core import vpn_database as vpn_database

pytestmark = pytest.mark.integration


@pytest.fixture(scope='module')
def redis_url():
    container = DockerContainer('redis:7-alpine').with_exposed_ports(6379)
    with container.waiting_for(LogMessageWaitStrategy('Ready to accept connections')):
        yield f'redis://{container.get_container_host_ip()}:{container.get_exposed_port(6379)}/0'


@pytest_asyncio.fixture
async def cache(redis_url):
    from app.infrastructure.adapters.cache.vpn import RedisVpnCache
    instance = RedisVpnCache(redis_url, VpnSettings(cache_timeout=0.1))
    try:
        yield instance
    finally:
        await instance.aclose()


async def test_negative_cache_fences_late_miss_after_create(cache):
    guid = uuid4()
    generation, missing = await cache.probe(guid)
    assert not missing
    await cache.invalidate(guid)
    await cache.remember_missing(guid, generation)
    assert not (await cache.probe(guid))[1]


async def test_negative_cache_hit_and_invalidation(cache):
    guid = uuid4()
    generation, _ = await cache.probe(guid)
    await cache.remember_missing(guid, generation)
    assert (await cache.probe(guid))[1]
    await cache.invalidate(guid)
    assert not (await cache.probe(guid))[1]


async def test_epoch_eviction_cannot_resurrect_old_negative(cache):
    guid = uuid4()
    generation, _ = await cache.probe(guid)
    await cache.remember_missing(guid, generation)
    await cache._redis.delete('vpn:negative:generation')
    assert not (await cache.probe(guid))[1]


async def test_redis_outage_falls_back_on_reads_and_fails_closed_on_invalidation():
    from app.infrastructure.adapters.cache.vpn import RedisVpnCache
    cache = RedisVpnCache('redis://127.0.0.1:1/0', VpnSettings(cache_timeout=0.05))
    try:
        assert await cache.probe(uuid4()) == (None, False)
        await cache.remember_missing(uuid4(), None)
        assert await cache.get_result('request') is None
        with pytest.raises(CacheUnavailableError):
            await cache.invalidate(uuid4())
    finally:
        await cache.aclose()


async def test_success_cache_retains_original_result(cache):
    value = VpnSubscription(user_guid=uuid4())
    await cache.set_result('success-' + str(value.user_guid), value)
    assert await cache.get_result('success-' + str(value.user_guid)) == value




async def test_late_database_miss_cannot_hide_successful_subscription(cache, core, vpn_database):
    from app.domain.errors.vpn_user import SubscriptionNotFoundError
    service, worker, _ = core
    service._cache = worker._cache = cache
    user = await service.register('a@example.com', 'Alice')
    with pytest.raises(SubscriptionNotFoundError):
        await service.get_subscription(user.guid)
    generation, missing = await cache.probe(user.guid)
    assert missing
    conn, _ = vpn_database
    assert await conn.fetchval('SELECT count(*) FROM vpn_subscriptions WHERE user_guid=$1', user.guid) == 0
    created = await service.create_subscription(user.guid, 'create-' + str(user.guid))
    await cache.remember_missing(user.guid, generation)
    assert await service.get_subscription(user.guid) == created


async def test_valid_cached_absence_does_not_query_postgres(cache, core, vpn_database):
    from app.domain.errors.vpn_user import SubscriptionNotFoundError
    service, _, _ = core
    service._cache = cache
    user = await service.register('a@example.com', 'Alice')
    with pytest.raises(SubscriptionNotFoundError):
        await service.get_subscription(user.guid)
    conn, _ = vpn_database
    await conn.execute('DROP TABLE vpn_subscriptions CASCADE')
    with pytest.raises(SubscriptionNotFoundError):
        await service.get_subscription(user.guid)


async def test_get_uses_database_when_redis_read_fails(core):
    from app.infrastructure.adapters.cache.vpn import RedisVpnCache
    service, _, _ = core
    user = await service.register('a@example.com', 'Alice')
    created = await service.create_subscription(user.guid, 'create-one')
    failed_cache = RedisVpnCache('redis://127.0.0.1:1/0', VpnSettings(cache_timeout=0.05))
    service._cache = failed_cache
    try:
        assert await service.get_subscription(user.guid) == created
    finally:
        await failed_cache.aclose()


async def test_valid_cached_absence_never_opens_uow_scope(cache, core):
    from app.domain.errors.vpn_user import SubscriptionNotFoundError
    service, _, _ = core
    service._cache = cache
    user = await service.register('a@example.com', 'Alice')
    with pytest.raises(SubscriptionNotFoundError):
        await service.get_subscription(user.guid)
    def unavailable_factory():
        raise AssertionError('Cached absence opened a database scope')
    service._uow_factory = unavailable_factory
    with pytest.raises(SubscriptionNotFoundError):
        await service.get_subscription(user.guid)
