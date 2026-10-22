from app.domain.common.handlers import RequestHandler
from app.domain.dto.vpn_user import VpnSubscription
from app.domain.errors.vpn_user import PanelUnavailableError, SubscriptionNotFoundError
from app.domain.ports.uow import UowFactory
from app.domain.ports.vpn_cache import VpnCachePort
from app.services.use_cases.subscriptions.get.request import GetSubscriptionRequest


class GetSubscriptionHandler(RequestHandler[GetSubscriptionRequest, VpnSubscription]):
    def __init__(self, uow_factory: UowFactory, cache: VpnCachePort):
        self._uow_factory = uow_factory
        self._cache = cache

    async def handle(self, request: GetSubscriptionRequest) -> VpnSubscription:
        generation, missing = await self._cache.probe(request.user_guid)
        if missing:
            raise SubscriptionNotFoundError()
        async with self._uow_factory() as uow:
            subscription = await uow.vpn_subscriptions.get(request.user_guid)
        if subscription is None:
            await self._cache.remember_missing(request.user_guid, generation)
            raise SubscriptionNotFoundError()
        if subscription.subscription_url is None:
            raise PanelUnavailableError()
        return subscription
