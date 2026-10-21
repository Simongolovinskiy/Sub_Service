import asyncio
import logging
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import timedelta
from uuid import UUID

from msgspec.structs import replace

from app.domain.common.timezone import get_current_datetime
from app.domain.dto.vpn_user import (
    PanelSubscription,
    VpnOutboxEvent,
    VpnSettings,
    VpnSubscription,
)
from app.domain.errors.vpn_user import CacheUnavailableError, PanelUnavailableError
from app.domain.ports.vpn_cache import VpnCachePort
from app.domain.ports.vpn_panel import VpnPanelPort
from app.infrastructure.ports.uow import UnitOfWorkPort

logger = logging.getLogger(__name__)
UowFactory = Callable[[], AbstractAsyncContextManager[UnitOfWorkPort]]


class VpnOutboxWorker:
    def __init__(self, uow_factory: UowFactory, panel: VpnPanelPort, cache: VpnCachePort, settings: VpnSettings):
        self._uow_factory = uow_factory
        self._panel = panel
        self._cache = cache
        self._settings = settings

    async def process_operation(self, guid: UUID, *, deadline: float | None = None) -> VpnSubscription:
        if deadline is None:
            deadline = asyncio.get_running_loop().time() + self._settings.operation_timeout
        try:
            async with asyncio.timeout_at(deadline):
                while True:
                    if asyncio.get_running_loop().time() >= deadline:
                        raise PanelUnavailableError()
                    _, error = await self._process(guid, deadline)
                    if error:
                        raise error
                    async with self._uow_factory() as uow:
                        event = await uow.outbox.get(guid)
                    if event.result is not None:
                        return event.result
                    if event.superseded_at is not None:
                        raise PanelUnavailableError()
                    await asyncio.sleep(self._settings.retry_initial)
        except TimeoutError:
            raise PanelUnavailableError() from None

    async def process_one(self) -> bool:
        deadline = asyncio.get_running_loop().time() + self._settings.operation_timeout
        processed, _ = await self._process(None, deadline)
        return processed

    async def drain(self, stop: asyncio.Event) -> None:
        while not stop.is_set() and await self.process_one():
            pass

    async def reconcile(self, stop: asyncio.Event) -> None:
        after = None
        while not stop.is_set():
            async with self._uow_factory() as uow:
                async with uow:
                    now = get_current_datetime()
                    current = await uow.vpn_subscriptions.claim_reconciliation(after, now)
                    if current is None or stop.is_set():
                        return
                    after = current.user_guid
                    status = 'DISABLED' if current.desired_expires_at <= now else current.desired_status
                    desired = replace(current, desired_status=status)
                    try:
                        async with asyncio.timeout(self._settings.operation_timeout):
                            result = await self._panel.reconcile(desired)
                    except (PanelUnavailableError, TimeoutError):
                        logger.warning('VPN reconciliation failed')
                        continue
                    await uow.vpn_subscriptions.save(replace(
                        current, subscription_url=result.subscription_url, expires_at=result.expires_at,
                        status='disabled' if status == 'DISABLED' else 'active', desired_status=status,
                        sync_status='synced', updated_at=get_current_datetime(),
                    ))

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            try:
                await self.drain(stop)
                await self.reconcile(stop)
            except Exception:
                logger.error('VPN outbox iteration failed')
            try:
                await asyncio.wait_for(stop.wait(), timeout=self._settings.polling_seconds)
            except TimeoutError:
                pass

    async def _process(self, guid: UUID | None, deadline: float) -> tuple[bool, Exception | None]:
        async with self._uow_factory() as uow:
            async with uow:
                now = get_current_datetime()
                event = await uow.outbox.claim(now, guid)
                if event is None:
                    return False, None
                current = await uow.vpn_subscriptions.get(event.user_guid)
                if event.action == 'expire' and event.version != current.version:
                    await uow.outbox.supersede(event.guid, now)
                    return True, None
                if event.action == 'expire' and (
                    current.expires_at is None or current.expires_at > now or current.status == 'disabled'
                ):
                    await uow.outbox.supersede(event.guid, now)
                    return True, None
                desired = replace(event.payload, external_id=current.external_id,
                                  subscription_url=current.subscription_url)
                if event.action == 'expire':
                    desired = replace(desired, desired_status='DISABLED')
                try:
                    if event.action == 'create':
                        await self._cache.invalidate(event.user_guid)
                    result = await self._deliver(event, desired, deadline)
                except (PanelUnavailableError, CacheUnavailableError, TimeoutError) as exc:
                    error = CacheUnavailableError() if isinstance(exc, CacheUnavailableError) else PanelUnavailableError()
                    delay = min(self._settings.retry_max,
                                self._settings.retry_initial * 2 ** min(event.attempts, 30))
                    await uow.outbox.retry(event.guid, get_current_datetime() + timedelta(seconds=delay),
                                           'cache_unavailable' if isinstance(error, CacheUnavailableError) else 'panel_unavailable')
                    await uow.vpn_subscriptions.save(replace(current, sync_status='error', updated_at=get_current_datetime()))
                    return True, error
                completed = replace(
                    current, external_id=result.external_id, subscription_url=result.subscription_url,
                    expires_at=result.expires_at,
                    desired_expires_at=(result.expires_at if event.action == 'create' else current.desired_expires_at),
                    status='disabled' if desired.desired_status == 'DISABLED' else 'active',
                    desired_status=desired.desired_status if event.version == current.version else current.desired_status,
                    sync_status='synced' if event.version == current.version else 'pending', updated_at=get_current_datetime(),
                )
                await uow.vpn_subscriptions.save(completed)
                await uow.outbox.publish(event.guid, completed, get_current_datetime())
                if event.action == 'create':
                    await uow.outbox.add(VpnOutboxEvent(
                        user_guid=event.user_guid, action='expire', version=event.version,
                        payload=completed, available_at=completed.expires_at,
                    ))
                return True, None

    async def _deliver(self, event: VpnOutboxEvent, desired: VpnSubscription, deadline: float) -> PanelSubscription:
        async with asyncio.timeout_at(deadline):
            delay = self._settings.retry_initial
            while True:
                if asyncio.get_running_loop().time() >= deadline:
                    raise PanelUnavailableError()
                try:
                    if event.action == 'create':
                        return await self._panel.create(
                            desired, get_current_datetime() + timedelta(days=self._settings.subscription_days)
                        )
                    return await self._panel.update(desired)
                except (PanelUnavailableError, TimeoutError):
                    if delay >= deadline - asyncio.get_running_loop().time():
                        raise PanelUnavailableError() from None
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, self._settings.retry_max)
