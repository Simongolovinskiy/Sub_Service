from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from msgspec import Struct, field

from app.domain.common.timezone import get_current_datetime


@dataclass(frozen=True)
class VpnSettings:
    subscription_days: int = 7
    traffic_limit_bytes: int = 0
    internal_squads: tuple[UUID, ...] = ()
    polling_seconds: float = 300
    operation_timeout: float = 2
    retry_initial: float = 0.2
    retry_max: float = 300
    negative_cache_ttl: int = 30
    idempotency_ttl: int = 86400
    cache_timeout: float = 0.2


class RegisteredVpnUser(Struct, frozen=True, kw_only=True):
    guid: UUID = field(default_factory=uuid4)
    email: str
    name: str
    registered_at: datetime = field(default_factory=get_current_datetime)


class VpnSubscription(Struct, frozen=True, kw_only=True):
    user_guid: UUID
    external_id: int | None = None
    subscription_url: str | None = None
    expires_at: datetime | None = None
    desired_expires_at: datetime | None = None
    status: Literal['pending', 'active', 'disabled'] = 'pending'
    desired_status: Literal['ACTIVE', 'DISABLED'] = 'ACTIVE'
    sync_status: Literal['pending', 'synced', 'error'] = 'pending'
    version: int = 1
    traffic_limit_bytes: int = 0
    internal_squads: tuple[UUID, ...] = ()
    created_at: datetime = field(default_factory=get_current_datetime)
    updated_at: datetime = field(default_factory=get_current_datetime)


class PanelSubscription(Struct, frozen=True, kw_only=True):
    external_id: int
    subscription_url: str
    expires_at: datetime
    status: Literal['ACTIVE', 'DISABLED', 'EXPIRED', 'LIMITED']


class VpnOutboxEvent(Struct, frozen=True, kw_only=True):
    guid: UUID = field(default_factory=uuid4)
    user_guid: UUID
    action: Literal['create', 'renew', 'expire']
    version: int
    request_id: str | None = None
    request_input: dict[str, Any] = field(default_factory=dict)
    payload: VpnSubscription
    result: VpnSubscription | None = None
    created_at: datetime = field(default_factory=get_current_datetime)
    available_at: datetime = field(default_factory=get_current_datetime)
    attempts: int = 0
    last_error: str | None = None
    published_at: datetime | None = None
    superseded_at: datetime | None = None
