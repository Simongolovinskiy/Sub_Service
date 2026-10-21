from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from dishka import AsyncContainer, Provider, Scope, provide

from app.domain.dto.vpn_user import VpnSettings
from app.domain.ports.vpn_cache import VpnCachePort
from app.domain.ports.vpn_panel import VpnPanelPort
from app.infrastructure.adapters.cache.vpn import RedisVpnCache
from app.infrastructure.adapters.vpn.remnawave import RemnawavePanel
from app.infrastructure.adapters.vpn.settings import VpnRuntimeSettings
from app.infrastructure.ports.uow import UnitOfWorkPort
from app.services.vpn_outbox import UowFactory, VpnOutboxWorker
from app.services.vpn_users import VpnUserService


class VpnProvider(Provider):
    scope = Scope.APP

    @provide
    def runtime_settings(self) -> VpnRuntimeSettings:
        return VpnRuntimeSettings()

    @provide
    def settings(self, settings: VpnRuntimeSettings) -> VpnSettings:
        return settings.domain_settings()

    @provide
    async def panel(self, settings: VpnRuntimeSettings) -> AsyncIterator[VpnPanelPort]:
        async with httpx.AsyncClient(
            base_url=settings.panel_base_url,
            headers={'Authorization': 'Bearer ' + settings.panel_token.get_secret_value()},
            timeout=settings.operation_timeout,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            yield RemnawavePanel(client)

    @provide
    async def cache(self, config: VpnRuntimeSettings, settings: VpnSettings) -> AsyncIterator[VpnCachePort]:
        cache = RedisVpnCache(config.redis_url.get_secret_value(), settings)
        try:
            yield cache
        finally:
            await cache.aclose()

    @provide
    def uow_factory(self, container: AsyncContainer) -> UowFactory:
        @asynccontextmanager
        async def factory() -> AsyncIterator[UnitOfWorkPort]:
            async with container() as scope:
                yield await scope.get(UnitOfWorkPort)
        return factory

    worker = provide(VpnOutboxWorker)
    service = provide(VpnUserService)
