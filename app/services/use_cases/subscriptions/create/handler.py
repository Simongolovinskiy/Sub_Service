from uuid import UUID

from app.domain.common.handlers import RequestHandler
from app.domain.dto.vpn_user import VpnSettings, VpnSubscription
from app.domain.errors.vpn_user import SubscriptionAlreadyExistsError
from app.domain.ports.uow import UnitOfWorkPort
from app.services.use_cases.subscriptions.create.request import CreateSubscriptionRequest
from app.services.use_cases.subscriptions.create.response import CreateSubscriptionResponse
from app.services.use_cases.subscriptions.mutation import SubscriptionMutation


class CreateSubscriptionHandler(RequestHandler[CreateSubscriptionRequest, CreateSubscriptionResponse]):
    def __init__(self, mutation: SubscriptionMutation, settings: VpnSettings):
        self._mutation = mutation
        self._settings = settings

    async def handle(self, request: CreateSubscriptionRequest) -> CreateSubscriptionResponse:
        subscription = await self._mutation.execute(request.user_guid, request.idempotency_key, 'create', self._prepare)
        return CreateSubscriptionResponse(
            subscription_url=subscription.subscription_url,
            expires_at=subscription.expires_at,
            status=subscription.status,
        )

    async def _prepare(self, uow: UnitOfWorkPort, user_guid: UUID) -> VpnSubscription:
        if await uow.vpn_subscriptions.get(user_guid) is not None:
            raise SubscriptionAlreadyExistsError()
        desired = VpnSubscription(user_guid=user_guid, traffic_limit_bytes=self._settings.traffic_limit_bytes,
                                  internal_squads=self._settings.internal_squads)
        await uow.vpn_subscriptions.add(desired)
        return desired
