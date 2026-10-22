import asyncio
from collections.abc import Awaitable, Callable
from typing import Literal
from uuid import UUID

from app.domain.dto.vpn_user import VpnOutboxEvent, VpnSettings, VpnSubscription
from app.domain.errors.vpn_user import (
    IdempotencyConflictError,
    PanelUnavailableError,
    VpnUserNotFoundError,
    VpnValidationError,
)
from app.domain.ports.uow import UnitOfWorkPort, UowFactory
from app.domain.ports.vpn_cache import VpnCachePort
from app.services.vpn_outbox import VpnOutboxWorker

PrepareSubscription = Callable[[UnitOfWorkPort, UUID], Awaitable[VpnSubscription]]


class SubscriptionMutation:
    def __init__(self, uow_factory: UowFactory, worker: VpnOutboxWorker, cache: VpnCachePort, settings: VpnSettings):
        self._uow_factory = uow_factory
        self._worker = worker
        self._cache = cache
        self._settings = settings

    async def execute(self, user_guid: UUID, key: str, action: Literal['create', 'renew'],
                      prepare: PrepareSubscription) -> VpnSubscription:
        deadline = asyncio.get_running_loop().time() + self._settings.operation_timeout
        try:
            async with asyncio.timeout_at(deadline):
                if not key.strip() or len(key) > 128:
                    raise VpnValidationError()
                request_input = {'user_guid': str(user_guid), 'action': action}
                async with self._uow_factory() as uow:
                    async with uow:
                        user = await uow.vpn_users.get(user_guid, lock=True)
                        if user is None:
                            raise VpnUserNotFoundError()
                        await uow.outbox.lock_request(key)
                        event = await uow.outbox.get_request(key)
                        if event is not None:
                            if event.request_input != request_input:
                                raise IdempotencyConflictError()
                        else:
                            desired = await prepare(uow, user_guid)
                            event = VpnOutboxEvent(user_guid=user_guid, action=action, version=desired.version,
                                                   request_id=key, request_input=request_input, payload=desired)
                            await uow.outbox.add(event)
                if event.result is not None:
                    cached = await self._cache.get_result(key)
                    return cached if cached is not None else event.result
                result = await self._worker.process_operation(event.guid, deadline=deadline)
                await self._cache.set_result(key, result)
                return result
        except TimeoutError:
            raise PanelUnavailableError() from None
