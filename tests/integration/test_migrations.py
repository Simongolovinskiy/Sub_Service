from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

import psycopg2
import pytest
from yoyo import get_backend, read_migrations

from app.infrastructure.adapters.persistence.rdb import migration_apply


def test_production_migration_track_is_vpn_only():
    migrations = read_migrations('migrations')
    assert [migration.id for migration in migrations] == ['000004_sub_service']


@pytest.fixture
def migration_database(database_url):
    schema = 'test_migrations_' + uuid4().hex
    connection = psycopg2.connect(database_url)
    connection.autocommit = True
    with connection.cursor() as cursor:
        cursor.execute(f'CREATE SCHEMA "{schema}"')
    url = urlsplit(database_url)
    query = urlencode(parse_qsl(url.query) + [('schema', schema)])
    dsn = urlunsplit(url._replace(query=query))
    backend = get_backend(dsn)
    try:
        yield backend, dsn
    finally:
        backend.connection.close()
        with connection.cursor() as cursor:
            cursor.execute(f'DROP SCHEMA "{schema}" CASCADE')
        connection.close()


def apply(backend, path):
    migrations = read_migrations(str(path))
    with backend.lock():
        backend.apply_migrations(backend.to_apply(migrations))


def seed_legacy(backend):
    apply(backend, 'tests/fixtures/migrations')
    backend.execute("INSERT INTO products (name,slug,price_cents,created_at,updated_at) VALUES ('sentinel','sentinel',1,now(),now())")
    backend.execute("INSERT INTO users (guid,username,password,created_at) VALUES (gen_random_uuid(),'sentinel','retained',now())")
    backend.execute("INSERT INTO vpn_users VALUES ('00000000-0000-0000-0000-000000000001','fixture@example.com','Fixture',now())")
    backend.execute("""INSERT INTO vpn_subscriptions
        (user_guid,external_id,subscription_url,expires_at,desired_expires_at,status,desired_status,
         sync_status,version,traffic_limit_bytes,internal_squads,created_at,updated_at)
        VALUES ('00000000-0000-0000-0000-000000000001',123,'https://example.com/sub',now()+interval '7 days',
                now()+interval '7 days','active','ACTIVE','synced',2,0,'{}',now(),now())""")
    backend.execute("""INSERT INTO outbox_events
        (guid,user_guid,action,version,request_id,request_input,payload,result,created_at,available_at,published_at)
        VALUES (gen_random_uuid(),'00000000-0000-0000-0000-000000000001','renew',2,'persistent-key',
                '{"days":7}','{"version":2}','{"status":"active"}',now(),now(),now())""")
    backend.connection.commit()


def snapshot(backend):
    return {
        table: backend.execute(f'SELECT row_to_json(t) FROM {table} t').fetchall()
        for table in ('products', 'users', 'vpn_users', 'vpn_subscriptions', 'outbox_events')
    }


def test_fresh_schema_contains_only_vpn_tables_and_reapply_is_safe(migration_database):
    backend, dsn = migration_database
    migration_apply.db_yoyo_migration(dsn)
    tables = {row[0] for row in backend.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname=current_schema()"
    ).fetchall()}
    assert {'vpn_users', 'vpn_subscriptions', 'outbox_events'} <= tables
    assert not {'users', 'products'} & tables
    applied = backend.get_applied_migration_hashes()
    migration_apply.db_yoyo_migration(dsn)
    assert backend.get_applied_migration_hashes() == applied


def test_legacy_upgrade_preserves_data_idempotency_and_history(migration_database):
    backend, dsn = migration_database
    seed_legacy(backend)
    before = snapshot(backend)
    history = set(backend.get_applied_migration_hashes())
    migration_apply.db_yoyo_migration(dsn)
    assert snapshot(backend) == before
    assert history < set(backend.get_applied_migration_hashes())
    applied = backend.get_applied_migration_hashes()
    migration_apply.db_yoyo_migration(dsn)
    assert snapshot(backend) == before
    assert backend.get_applied_migration_hashes() == applied


def test_failed_followup_rollback_preserves_preexisting_vpn_data(migration_database, tmp_path, monkeypatch):
    backend, dsn = migration_database
    seed_legacy(backend)
    before = snapshot(backend)
    history = backend.get_applied_migration_hashes()
    for source in Path('migrations').glob('*.sql'):
        (tmp_path / source.name).write_bytes(source.read_bytes())
    (tmp_path / '000005_failure.sql').write_text(
        '-- depends: 000004_sub_service\nSELECT missing_column FROM vpn_users;\n'
    )
    monkeypatch.setattr(migration_apply, 'MIGRATIONS_DIR', str(tmp_path))
    with pytest.raises(psycopg2.errors.UndefinedColumn):
        migration_apply.db_yoyo_migration(dsn)
    assert snapshot(backend) == before
    assert backend.get_applied_migration_hashes() == history
    monkeypatch.setattr(migration_apply, 'MIGRATIONS_DIR', str(Path('migrations').resolve()))
    migration_apply.db_yoyo_migration(dsn)
    assert snapshot(backend) == before
