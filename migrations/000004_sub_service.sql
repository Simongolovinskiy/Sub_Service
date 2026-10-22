CREATE TABLE IF NOT EXISTS vpn_users (
    guid UUID PRIMARY KEY,
    email TEXT NOT NULL UNIQUE CHECK (email = lower(btrim(email)) AND email <> ''),
    name TEXT NOT NULL CHECK (btrim(name) <> ''),
    registered_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS vpn_subscriptions (
    user_guid UUID PRIMARY KEY REFERENCES vpn_users (guid),
    external_id BIGINT UNIQUE,
    subscription_url TEXT,
    expires_at TIMESTAMPTZ,
    desired_expires_at TIMESTAMPTZ,
    status TEXT NOT NULL CHECK (status IN ('pending', 'active', 'disabled')),
    desired_status TEXT NOT NULL CHECK (desired_status IN ('ACTIVE', 'DISABLED')),
    sync_status TEXT NOT NULL CHECK (sync_status IN ('pending', 'synced', 'error')),
    version BIGINT NOT NULL CHECK (version > 0),
    traffic_limit_bytes BIGINT NOT NULL CHECK (traffic_limit_bytes >= 0),
    internal_squads UUID[] NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS outbox_events (
    guid UUID PRIMARY KEY,
    user_guid UUID NOT NULL REFERENCES vpn_users (guid),
    action TEXT NOT NULL CHECK (action IN ('create', 'renew', 'expire')),
    version BIGINT NOT NULL,
    request_id TEXT UNIQUE CHECK (request_id IS NULL OR length(btrim(request_id)) BETWEEN 1 AND 128),
    request_input JSONB NOT NULL,
    payload JSONB NOT NULL,
    result JSONB,
    created_at TIMESTAMPTZ NOT NULL,
    available_at TIMESTAMPTZ NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    published_at TIMESTAMPTZ,
    superseded_at TIMESTAMPTZ,
    CHECK (published_at IS NULL OR superseded_at IS NULL)
);
CREATE INDEX IF NOT EXISTS idx_outbox_pending ON outbox_events (available_at, created_at)
    WHERE published_at IS NULL AND superseded_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_outbox_user_pending ON outbox_events (user_guid, created_at)
    WHERE published_at IS NULL AND superseded_at IS NULL;
