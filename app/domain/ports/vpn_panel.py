from datetime import datetime
from typing import Protocol

from app.domain.dto.vpn_user import PanelSubscription, VpnSubscription


class VpnPanelPort(Protocol):
    async def create(self, subscription: VpnSubscription, expires_at: datetime) -> PanelSubscription: ...
    async def update(self, subscription: VpnSubscription) -> PanelSubscription: ...
    async def reconcile(self, subscription: VpnSubscription) -> PanelSubscription: ...
