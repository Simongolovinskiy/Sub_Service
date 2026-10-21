import asyncio
from datetime import timedelta
from uuid import UUID

from msgspec.structs import replace

from app.domain.common.timezone import get_current_datetime
from app.domain.dto.vpn_user import (
    RegisteredVpnUser,
    VpnOutboxEvent,
    VpnSettings,
    VpnSubscription,
)
from app.domain.errors.vpn_user import (
    IdempotencyConflictError,
    PanelUnavailableError,
    SubscriptionAlreadyExistsError,
    SubscriptionNotFoundError,
    VpnUserNotFoundError,
    VpnValidationError,
)
from app.domain.ports.vpn_cache import VpnCachePort
from app.services.vpn_outbox import UowFactory, VpnOutboxWorker


class VpnUserService:
    def __init__(self, uow_factory: UowFactory, worker: VpnOutboxWorker, cache: VpnCachePort, settings: VpnSettings):
        self._uow_factory = uow_factory
        self._worker = worker
        self._cache = cache
        self._settings = settings

    async def register(self, email: str, name: str) -> RegisteredVpnUser:
        email, name = email.strip().lower(), name.strip()
        if not name or len(name) > 200 or len(email) > 254 or email.count('@') != 1 or not all(email.split('@')):
            raise VpnValidationError()
        user = RegisteredVpnUser(email=email, name=name)
        async with self._uow_factory() as uow:
            async with uow:
                await uow.vpn_users.add(user)
        return user

    async def get_subscription(self, user_guid: UUID) -> VpnSubscription:
        generation, missing = await self._cache.probe(user_guid)
        if missing:
            raise SubscriptionNotFoundError()
        async with self._uow_factory() as uow:
            subscription = await uow.vpn_subscriptions.get(user_guid)
        if subscription is None:
            await self._cache.remember_missing(user_guid, generation)
            raise SubscriptionNotFoundError()
        if subscription.subscription_url is None:
            raise PanelUnavailableError()
        return subscription

    async def create_subscription(self, user_guid: UUID, idempotency_key: str) -> VpnSubscription:
        return await self._subscription_operation(user_guid, idempotency_key, 'create')

    async def extend_subscription(self, user_guid: UUID, idempotency_key: str) -> VpnSubscription:
        return await self._subscription_operation(user_guid, idempotency_key, 'renew')

    async def _subscription_operation(self, user_guid: UUID, key: str, action: str) -> VpnSubscription:
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
                            current = await uow.vpn_subscriptions.get(user_guid)
                            now = get_current_datetime()
                            if action == 'create':
                                if current is not None:
                                    raise SubscriptionAlreadyExistsError()
                                desired = VpnSubscription(user_guid=user_guid, traffic_limit_bytes=self._settings.traffic_limit_bytes,
                                                          internal_squads=self._settings.internal_squads)
                                await uow.vpn_subscriptions.add(desired)
                            else:
                                if current is None:
                                    raise SubscriptionNotFoundError()
                                if current.expires_at is None:
                                    raise PanelUnavailableError()
                                target = max(now, current.desired_expires_at or current.expires_at) + timedelta(days=self._settings.subscription_days)
                                desired = replace(current, desired_expires_at=target, desired_status='ACTIVE',
                                                  sync_status='pending', version=current.version + 1, updated_at=now)
                                await uow.vpn_subscriptions.save(desired)
                                await uow.outbox.add(VpnOutboxEvent(
                                    user_guid=user_guid, action='expire', version=desired.version,
                                    payload=desired, available_at=target,
                                ))
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
