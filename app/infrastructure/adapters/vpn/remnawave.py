from datetime import UTC, datetime
from typing import Annotated, Literal

import httpx
from msgspec import Meta, Struct, ValidationError, json

from app.domain.common.timezone import get_current_datetime
from app.domain.dto.vpn_user import PanelSubscription, VpnSubscription
from app.domain.errors.vpn_user import PanelUnavailableError
from app.domain.ports.vpn_panel import VpnPanelPort


class _RemoteUser(Struct):
    id: int
    username: str
    description: str | None
    subscriptionUrl: str
    expireAt: Annotated[datetime, Meta(tz=True)]
    status: Literal['ACTIVE', 'DISABLED', 'EXPIRED', 'LIMITED']


class _RemoteResponse(Struct):
    response: _RemoteUser


class RemnawavePanel(VpnPanelPort):
    def __init__(self, client: httpx.AsyncClient):
        self._client = client

    async def create(self, subscription: VpnSubscription, expires_at: datetime) -> PanelSubscription:
        existing = await self._find(subscription)
        if existing is not None:
            return existing
        body = {
            'username': 'vpn_' + subscription.user_guid.hex,
            'description': 'asyncpytemplate:' + str(subscription.user_guid),
            'expireAt': expires_at.astimezone(UTC).isoformat().replace('+00:00', 'Z'),
            'status': 'ACTIVE', 'trafficLimitBytes': subscription.traffic_limit_bytes,
            'activeInternalSquads': [str(squad) for squad in subscription.internal_squads],
        }
        try:
            response = await self._client.post('/api/users', json=body)
        except httpx.RequestError:
            existing = await self._find(subscription)
            if existing is not None:
                return existing
            raise PanelUnavailableError() from None
        if response.status_code == 409:
            existing = await self._find(subscription)
            if existing is not None:
                return existing
        if response.status_code != 201:
            raise PanelUnavailableError()
        return self._decode(response, subscription)

    async def update(self, subscription: VpnSubscription) -> PanelSubscription:
        body = {
            'id': subscription.external_id,
            'status': subscription.desired_status,
        }
        if subscription.desired_status != 'DISABLED' or subscription.desired_expires_at > get_current_datetime():
            body['expireAt'] = subscription.desired_expires_at.astimezone(UTC).isoformat().replace('+00:00', 'Z')
        try:
            response = await self._client.patch('/api/users', json=body)
        except httpx.RequestError:
            raise PanelUnavailableError() from None
        if response.status_code != 200:
            raise PanelUnavailableError()
        return self._decode(response, subscription, expected_expiry=subscription.desired_expires_at)

    async def reconcile(self, subscription: VpnSubscription) -> PanelSubscription:
        current = await self._find(subscription)
        if current is None:
            raise PanelUnavailableError()
        actual = current.expires_at.replace(microsecond=current.expires_at.microsecond // 1000 * 1000)
        target = subscription.desired_expires_at.replace(microsecond=subscription.desired_expires_at.microsecond // 1000 * 1000)
        if actual == target and current.status == subscription.desired_status:
            return current
        return await self.update(subscription)

    async def _find(self, subscription: VpnSubscription) -> PanelSubscription | None:
        try:
            response = await self._client.get('/api/users/by-username/vpn_' + subscription.user_guid.hex)
        except httpx.RequestError:
            raise PanelUnavailableError() from None
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise PanelUnavailableError()
        return self._decode(response, subscription)

    @staticmethod
    def _decode(response: httpx.Response, subscription: VpnSubscription,
                *, expected_expiry: datetime | None = None) -> PanelSubscription:
        try:
            user = json.decode(response.content, type=_RemoteResponse).response
        except (ValidationError, ValueError):
            raise PanelUnavailableError() from None
        if (user.id <= 0 or not user.subscriptionUrl
            or user.username != 'vpn_' + subscription.user_guid.hex
            or user.description != 'asyncpytemplate:' + str(subscription.user_guid)
            or (subscription.external_id is not None and subscription.external_id != user.id)):
            raise PanelUnavailableError()
        actual_expiry = user.expireAt.astimezone(UTC)
        if expected_expiry is not None:
            expected_expiry = expected_expiry.astimezone(UTC)
            actual_milliseconds = actual_expiry.replace(microsecond=actual_expiry.microsecond // 1000 * 1000)
            expected_milliseconds = expected_expiry.replace(microsecond=expected_expiry.microsecond // 1000 * 1000)
            expired_active = (subscription.desired_status == 'ACTIVE' and user.status == 'EXPIRED'
                              and expected_expiry <= get_current_datetime())
            if actual_milliseconds != expected_milliseconds or (user.status != subscription.desired_status and not expired_active):
                raise PanelUnavailableError()
        return PanelSubscription(external_id=user.id, subscription_url=user.subscriptionUrl,
                                 expires_at=actual_expiry, status=user.status)
