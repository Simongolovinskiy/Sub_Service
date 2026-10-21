import asyncio
from hashlib import sha256
from uuid import UUID, uuid4

from msgspec import DecodeError, json
from redis.asyncio import Redis
from redis.asyncio.retry import Retry
from redis.backoff import NoBackoff
from redis.exceptions import RedisError

from app.domain.dto.vpn_user import VpnSettings, VpnSubscription
from app.domain.errors.vpn_user import CacheUnavailableError
from app.domain.ports.vpn_cache import VpnCachePort

_GENERATION = 'vpn:negative:generation'
_PROBE = '''
local generation = redis.call('GET', KEYS[1])
if not generation then
    generation = ARGV[1]
    redis.call('SET', KEYS[1], generation)
end
local missing = redis.call('GET', KEYS[2])
return {generation, missing == generation and 1 or 0}
'''
_REMEMBER = '''
if redis.call('GET', KEYS[1]) == ARGV[1] then
    redis.call('SET', KEYS[2], ARGV[1], 'EX', ARGV[2])
    return 1
end
return 0
'''
_INVALIDATE = '''
redis.call('SET', KEYS[1], ARGV[1])
redis.call('DEL', KEYS[2])
return 1
'''


class RedisVpnCache(VpnCachePort):
    def __init__(self, url: str, settings: VpnSettings):
        self._settings = settings
        self._redis = Redis.from_url(
            url, socket_connect_timeout=settings.cache_timeout, socket_timeout=settings.cache_timeout,
            retry=Retry(NoBackoff(), 0),
        )

    async def probe(self, user_guid: UUID) -> tuple[str | None, bool]:
        try:
            async with asyncio.timeout(self._settings.cache_timeout):
                generation, missing = await self._redis.eval(
                    _PROBE, 2, _GENERATION, 'vpn:missing:' + str(user_guid), uuid4().hex,
                )
                return generation.decode(), bool(missing)
        except (RedisError, TimeoutError):
            return None, False

    async def remember_missing(self, user_guid: UUID, generation: str | None) -> None:
        if generation is None:
            return
        try:
            async with asyncio.timeout(self._settings.cache_timeout):
                await self._redis.eval(_REMEMBER, 2, _GENERATION, 'vpn:missing:' + str(user_guid),
                                       generation, self._settings.negative_cache_ttl)
        except (RedisError, TimeoutError):
            pass

    async def invalidate(self, user_guid: UUID) -> None:
        try:
            async with asyncio.timeout(self._settings.cache_timeout):
                await self._redis.eval(_INVALIDATE, 2, _GENERATION, 'vpn:missing:' + str(user_guid), uuid4().hex)
        except (RedisError, TimeoutError):
            raise CacheUnavailableError() from None

    async def get_result(self, key: str) -> VpnSubscription | None:
        try:
            async with asyncio.timeout(self._settings.cache_timeout):
                value = await self._redis.get('vpn:result:' + sha256(key.encode()).hexdigest())
            return json.decode(value, type=VpnSubscription) if value else None
        except (RedisError, TimeoutError, DecodeError):
            return None

    async def set_result(self, key: str, result: VpnSubscription) -> None:
        try:
            async with asyncio.timeout(self._settings.cache_timeout):
                await self._redis.set('vpn:result:' + sha256(key.encode()).hexdigest(),
                                      json.encode(result), ex=self._settings.idempotency_ttl)
        except (RedisError, TimeoutError):
            pass

    async def aclose(self) -> None:
        await self._redis.aclose()
