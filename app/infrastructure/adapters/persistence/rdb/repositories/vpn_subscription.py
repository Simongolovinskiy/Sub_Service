from datetime import datetime
from uuid import UUID

import asyncpg

from app.domain.dto.vpn_user import VpnSubscription
from app.domain.ports.repositories.vpn_subscription import VpnSubscriptionRepositoryPort


class RDBVpnSubscriptionRepository(VpnSubscriptionRepositoryPort):
    def __init__(self, conn: asyncpg.Connection):
        self._conn = conn

    async def add(self, subscription: VpnSubscription) -> None:
        await self._conn.execute(
            '''INSERT INTO vpn_subscriptions
               (user_guid,external_id,subscription_url,expires_at,desired_expires_at,status,
                desired_status,sync_status,version,traffic_limit_bytes,internal_squads,created_at,updated_at)
               VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)''',
            subscription.user_guid, subscription.external_id, subscription.subscription_url,
            subscription.expires_at, subscription.desired_expires_at, subscription.status,
            subscription.desired_status, subscription.sync_status, subscription.version,
            subscription.traffic_limit_bytes, subscription.internal_squads,
            subscription.created_at, subscription.updated_at,
        )

    async def get(self, user_guid: UUID) -> VpnSubscription | None:
        row = await self._conn.fetchrow('SELECT * FROM vpn_subscriptions WHERE user_guid=$1', user_guid)
        if row is None:
            return None
        values = dict(row)
        values['internal_squads'] = tuple(values['internal_squads'])
        return VpnSubscription(**values)

    async def save(self, subscription: VpnSubscription) -> None:
        await self._conn.execute(
            '''UPDATE vpn_subscriptions SET external_id=$2,subscription_url=$3,expires_at=$4,
               desired_expires_at=$5,status=$6,desired_status=$7,sync_status=$8,version=$9,updated_at=$10
               WHERE user_guid=$1''',
            subscription.user_guid, subscription.external_id, subscription.subscription_url,
            subscription.expires_at, subscription.desired_expires_at, subscription.status,
            subscription.desired_status, subscription.sync_status, subscription.version, subscription.updated_at,
        )

    async def claim_reconciliation(self, after: UUID | None, now: datetime) -> VpnSubscription | None:
        row = await self._conn.fetchrow(
            """SELECT s.* FROM vpn_subscriptions s JOIN vpn_users u ON u.guid=s.user_guid
               WHERE ($1::uuid IS NULL OR s.user_guid > $1)
                 AND s.external_id IS NOT NULL AND s.desired_expires_at IS NOT NULL
                 AND NOT EXISTS (
                   SELECT 1 FROM outbox_events e WHERE e.user_guid=s.user_guid
                     AND e.published_at IS NULL AND e.superseded_at IS NULL
                     AND (e.action IN ('create','renew') OR (e.action='expire' AND e.available_at <= $2))
                 )
               ORDER BY s.user_guid LIMIT 1 FOR UPDATE OF s,u SKIP LOCKED""", after, now,
        )
        if row is None:
            return None
        values = dict(row)
        values['internal_squads'] = tuple(values['internal_squads'])
        return VpnSubscription(**values)
