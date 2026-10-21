from datetime import datetime
from uuid import UUID

import asyncpg
from msgspec import json

from app.domain.dto.vpn_user import VpnOutboxEvent, VpnSubscription
from app.domain.ports.repositories.outbox import OutboxRepositoryPort


class RDBOutboxRepository(OutboxRepositoryPort):
    def __init__(self, conn: asyncpg.Connection):
        self._conn = conn

    async def lock_request(self, request_id: str) -> None:
        await self._conn.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))', request_id)

    async def get_request(self, request_id: str) -> VpnOutboxEvent | None:
        row = await self._conn.fetchrow('SELECT * FROM outbox_events WHERE request_id=$1', request_id)
        return self._event(row) if row else None

    async def get(self, guid: UUID) -> VpnOutboxEvent | None:
        row = await self._conn.fetchrow('SELECT * FROM outbox_events WHERE guid=$1', guid)
        return self._event(row) if row else None

    async def add(self, event: VpnOutboxEvent) -> None:
        await self._conn.execute(
            '''INSERT INTO outbox_events (guid,user_guid,action,version,request_id,request_input,payload,
               created_at,available_at) VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7::jsonb,$8,$9)''',
            event.guid, event.user_guid, event.action, event.version, event.request_id,
            json.encode(event.request_input).decode(), json.encode(event.payload).decode(),
            event.created_at, event.available_at,
        )

    async def claim(self, now: datetime, guid: UUID | None = None) -> VpnOutboxEvent | None:
        row = await self._conn.fetchrow(
            '''SELECT e.* FROM outbox_events e
               JOIN vpn_users u ON u.guid=e.user_guid
               JOIN vpn_subscriptions s ON s.user_guid=e.user_guid
               WHERE e.published_at IS NULL AND e.superseded_at IS NULL
                 AND (($2::uuid IS NULL AND e.available_at <= $1)
                   OR (e.user_guid=(SELECT user_guid FROM outbox_events WHERE guid=$2)
                       AND e.version <= (SELECT version FROM outbox_events WHERE guid=$2)
                       AND e.action IN ('create','renew')))
                 AND NOT EXISTS (
                   SELECT 1 FROM outbox_events previous
                   WHERE previous.user_guid=e.user_guid AND previous.action IN ('create','renew')
                     AND (previous.version < e.version OR (e.action='expire' AND previous.version=e.version))
                     AND previous.published_at IS NULL AND previous.superseded_at IS NULL
                 )
               ORDER BY CASE WHEN $2::uuid IS NOT NULL THEN e.version END,e.available_at,e.created_at,e.guid
               LIMIT 1 FOR UPDATE OF e,u,s SKIP LOCKED''', now, guid,
        )
        return self._event(row) if row else None

    async def publish(self, guid: UUID, result: VpnSubscription, now: datetime) -> None:
        await self._conn.execute(
            'UPDATE outbox_events SET published_at=$2,result=$3::jsonb,last_error=NULL WHERE guid=$1',
            guid, now, json.encode(result).decode(),
        )

    async def supersede(self, guid: UUID, now: datetime) -> None:
        await self._conn.execute('UPDATE outbox_events SET superseded_at=$2 WHERE guid=$1', guid, now)

    async def retry(self, guid: UUID, available_at: datetime, error: str) -> None:
        await self._conn.execute(
            'UPDATE outbox_events SET attempts=attempts+1,available_at=$2,last_error=$3 WHERE guid=$1',
            guid, available_at, error,
        )

    @staticmethod
    def _event(row: asyncpg.Record) -> VpnOutboxEvent:
        values = dict(row)
        values['request_input'] = json.decode(values['request_input'])
        values['payload'] = json.decode(values['payload'], type=VpnSubscription)
        if values['result'] is not None:
            values['result'] = json.decode(values['result'], type=VpnSubscription)
        return VpnOutboxEvent(**values)
