from datetime import timedelta
from uuid import UUID

from msgspec.structs import replace

from app.domain.common.handlers import RequestHandler
from app.domain.common.timezone import get_current_datetime
from app.domain.dto.vpn_user import VpnOutboxEvent, VpnSettings, VpnSubscription
from app.domain.errors.vpn_user import PanelUnavailableError, SubscriptionNotFoundError
from app.domain.ports.uow import UnitOfWorkPort
from app.services.use_cases.subscriptions.extend.request import ExtendSubscriptionRequest
from app.services.use_cases.subscriptions.extend.response import ExtendSubscriptionResponse
from app.services.use_cases.subscriptions.mutation import SubscriptionMutation


class ExtendSubscriptionHandler(RequestHandler[ExtendSubscriptionRequest, ExtendSubscriptionResponse]):
    def __init__(self, mutation: SubscriptionMutation, settings: VpnSettings):
        self._mutation = mutation
        self._settings = settings

    async def handle(self, request: ExtendSubscriptionRequest) -> ExtendSubscriptionResponse:
        subscription = await self._mutation.execute(request.user_guid, request.idempotency_key, 'renew', self._prepare)
        return ExtendSubscriptionResponse(
            subscription_url=subscription.subscription_url,
            expires_at=subscription.expires_at,
            status=subscription.status,
        )

    async def _prepare(self, uow: UnitOfWorkPort, user_guid: UUID) -> VpnSubscription:
        current = await uow.vpn_subscriptions.get(user_guid)
        now = get_current_datetime()
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
        return desired
