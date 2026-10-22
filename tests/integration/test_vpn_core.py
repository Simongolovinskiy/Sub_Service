import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import asyncpg
import pytest
import pytest_asyncio

from app.services.use_cases.subscriptions.create.request import CreateSubscriptionRequest
from app.services.use_cases.subscriptions.extend.request import ExtendSubscriptionRequest
from app.services.use_cases.subscriptions.get.request import GetSubscriptionRequest
from app.services.use_cases.users.register.request import RegisterUserRequest

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture
async def vpn_database(database_url):
    conn = await asyncpg.connect(database_url)
    schema = "test_vpn_" + uuid4().hex
    await conn.execute(f'CREATE SCHEMA "{schema}"')
    await conn.execute(f'SET search_path TO "{schema}"')
    await conn.execute(Path("migrations/000004_sub_service.sql").read_text())
    pool = await asyncpg.create_pool(
        database_url, min_size=1, max_size=4, server_settings={"search_path": schema}
    )
    try:
        yield conn, pool
    finally:
        await pool.close()
        await conn.execute(f'DROP SCHEMA "{schema}" CASCADE')
        await conn.close()


async def test_fresh_schema_contains_only_vpn_tables(vpn_database):
    conn, _ = vpn_database
    assert await conn.fetchval("SELECT to_regclass('vpn_users')") == "vpn_users"
    assert await conn.fetchval("SELECT to_regclass('outbox_events')") == "outbox_events"
    assert await conn.fetchval("SELECT to_regclass('vpn_subscriptions')") == "vpn_subscriptions"
    assert await conn.fetchval("SELECT to_regclass('users')") is None
    assert await conn.fetchval("SELECT to_regclass('products')") is None


def make_uow(conn):
    from app.infrastructure.adapters.persistence.rdb.repositories.outbox import (
        RDBOutboxRepository,
    )
    from app.infrastructure.adapters.persistence.rdb.repositories.vpn_subscription import (
        RDBVpnSubscriptionRepository,
    )
    from app.infrastructure.adapters.persistence.rdb.repositories.vpn_user import (
        RDBVpnUserRepository,
    )
    from app.infrastructure.adapters.persistence.rdb.uow import RDBUnitOfWork

    return RDBUnitOfWork(conn, RDBVpnUserRepository(conn), RDBVpnSubscriptionRepository(conn), RDBOutboxRepository(conn))


class NoCache:
    async def probe(self, guid):
        return "0", False

    async def remember_missing(self, guid, generation):
        pass

    async def invalidate(self, guid):
        pass

    async def get_result(self, key):
        return None

    async def set_result(self, key, result):
        pass


class FakePanel:
    def __init__(self):
        self.users = {}
        self.calls = []
        self.create_timeout = False
        self.fail = False
        self.started = None
        self.release = None

    async def create(self, subscription, expires_at):
        from app.domain.dto.vpn_user import PanelSubscription
        if self.fail:
            from app.domain.errors.vpn_user import PanelUnavailableError
            raise PanelUnavailableError()
        if self.started:
            self.started.set()
            await self.release.wait()
        if subscription.user_guid not in self.users:
            self.users[subscription.user_guid] = PanelSubscription(
                external_id=len(self.users) + 1, subscription_url="https://subscription.invalid/key",
                expires_at=expires_at, status='ACTIVE',
            )
            self.calls.append(("create", subscription.user_guid, expires_at))
        if self.create_timeout:
            self.create_timeout = False
            raise TimeoutError("must not persist this secret")
        return self.users[subscription.user_guid]

    async def update(self, subscription):
        from app.domain.dto.vpn_user import PanelSubscription
        if self.fail:
            from app.domain.errors.vpn_user import PanelUnavailableError
            raise PanelUnavailableError()
        self.calls.append(("update", subscription.user_guid, subscription.desired_expires_at,
                           subscription.desired_status))
        current = self.users[subscription.user_guid]
        result = PanelSubscription(external_id=current.external_id, subscription_url=current.subscription_url,
                                   expires_at=subscription.desired_expires_at, status=subscription.desired_status)
        self.users[subscription.user_guid] = result
        return result


    async def reconcile(self, subscription):
        if self.fail:
            from app.domain.errors.vpn_user import PanelUnavailableError
            raise PanelUnavailableError()
        current = self.users[subscription.user_guid]
        if current.expires_at != subscription.desired_expires_at or current.status != subscription.desired_status:
            return await self.update(subscription)
        return current


@pytest_asyncio.fixture
async def core(vpn_database):
    from app.domain.dto.vpn_user import VpnSettings
    from app.services.vpn_outbox import VpnOutboxWorker
    from app.services.use_cases.subscriptions.create.handler import CreateSubscriptionHandler
    from app.services.use_cases.subscriptions.extend.handler import ExtendSubscriptionHandler
    from app.services.use_cases.subscriptions.get.handler import GetSubscriptionHandler
    from app.services.use_cases.subscriptions.mutation import SubscriptionMutation
    from app.services.use_cases.users.register.handler import RegisterUserHandler
    conn, pool = vpn_database
    @asynccontextmanager
    async def factory():
        async with pool.acquire() as connection:
            yield make_uow(connection)
    panel = FakePanel()
    settings = VpnSettings(operation_timeout=0.08, retry_initial=0.01)
    worker = VpnOutboxWorker(factory, panel, NoCache(), settings)
    cache = NoCache()
    mutation = SubscriptionMutation(factory, worker, cache, settings)
    handlers = SimpleNamespace(
        register=RegisterUserHandler(factory),
        create=CreateSubscriptionHandler(mutation, settings),
        get=GetSubscriptionHandler(factory, cache),
        extend=ExtendSubscriptionHandler(mutation, settings),
        mutation=mutation,
    )
    return handlers, worker, panel


async def test_use_case_outputs_have_explicit_public_contracts(core):
    from msgspec import to_builtins

    from app.services.use_cases.subscriptions.create.response import CreateSubscriptionResponse
    from app.services.use_cases.subscriptions.extend.response import ExtendSubscriptionResponse
    from app.services.use_cases.subscriptions.get.response import GetSubscriptionResponse
    from app.services.use_cases.users.register.response import RegisterUserResponse

    handlers, _, _ = core
    user = await handlers.register.handle(RegisterUserRequest('contracts@example.com', 'Contracts'))
    assert type(user) is RegisterUserResponse
    assert set(to_builtins(user)) == {'id', 'registered_at'}
    created = await handlers.create.handle(CreateSubscriptionRequest(user.id, 'contracts-create'))
    fetched = await handlers.get.handle(GetSubscriptionRequest(user.id))
    extended = await handlers.extend.handle(ExtendSubscriptionRequest(user.id, 'contracts-extend'))
    for result, response_type in ((created, CreateSubscriptionResponse), (fetched, GetSubscriptionResponse),
                                  (extended, ExtendSubscriptionResponse)):
        assert type(result) is response_type
        assert set(to_builtins(result)) == {'subscription_url', 'expires_at', 'status'}
        with pytest.raises(AttributeError):
            result.status = 'disabled'
    assert to_builtins(fetched) == to_builtins(created)
    assert extended.expires_at == created.expires_at + timedelta(days=7)


async def test_registration_and_subscription_intent_are_atomic(vpn_database, core):
    conn, _ = vpn_database
    handlers, _, panel = core
    user = await handlers.register.handle(RegisterUserRequest("  A@Example.COM  ", "Alice"))
    assert await conn.fetchval('SELECT email FROM vpn_users WHERE guid=$1', user.id) == "a@example.com"
    panel.fail = True
    from app.domain.errors.vpn_user import PanelUnavailableError
    with pytest.raises(PanelUnavailableError):
        await handlers.create.handle(CreateSubscriptionRequest(user.id, "create-one"))
    assert await conn.fetchval("SELECT count(*) FROM vpn_subscriptions") == 1
    assert await conn.fetchval("SELECT count(*) FROM outbox_events WHERE action='create'") == 1
    assert await conn.fetchval("SELECT published_at FROM outbox_events WHERE action='create'") is None


async def test_event_insert_failure_rolls_back_subscription(vpn_database, core):
    conn, _ = vpn_database
    handlers, _, _ = core
    user = await handlers.register.handle(RegisterUserRequest("a@example.com", "Alice"))
    await conn.execute("ALTER TABLE outbox_events ADD CONSTRAINT reject_events CHECK (action <> 'create')")
    with pytest.raises(asyncpg.CheckViolationError):
        await handlers.create.handle(CreateSubscriptionRequest(user.id, "create-one"))
    assert await conn.fetchval("SELECT count(*) FROM vpn_subscriptions") == 0


async def test_replay_renewal_uses_original_absolute_target(vpn_database, core):
    conn, _ = vpn_database
    handlers, _, panel = core
    user = await handlers.register.handle(RegisterUserRequest("a@example.com", "Alice"))
    created = await handlers.create.handle(CreateSubscriptionRequest(user.id, "create-one"))
    renewed = await handlers.extend.handle(ExtendSubscriptionRequest(user.id, "renew-one"))
    replay = await handlers.extend.handle(ExtendSubscriptionRequest(user.id, "renew-one"))
    assert renewed.expires_at == created.expires_at + timedelta(days=7)
    assert replay == renewed
    assert len(panel.calls) == 2
    assert await conn.fetchval("SELECT count(*) FROM outbox_events WHERE action='renew'") == 1
    from app.domain.errors.vpn_user import IdempotencyConflictError
    with pytest.raises(IdempotencyConflictError):
        await handlers.create.handle(CreateSubscriptionRequest(user.id, "renew-one"))
    other = await handlers.register.handle(RegisterUserRequest("b@example.com", "Bob"))
    with pytest.raises(IdempotencyConflictError):
        await handlers.extend.handle(ExtendSubscriptionRequest(other.id, "renew-one"))


async def test_timeout_after_panel_create_recovers_without_duplicate(vpn_database, core):
    conn, _ = vpn_database
    handlers, _, panel = core
    user = await handlers.register.handle(RegisterUserRequest("a@example.com", "Alice"))
    panel.create_timeout = True
    result = await handlers.create.handle(CreateSubscriptionRequest(user.id, "create-one"))
    assert await conn.fetchval('SELECT external_id FROM vpn_subscriptions WHERE user_guid=$1', user.id) == 1
    assert len(panel.calls) == 1
    assert await conn.fetchval("SELECT published_at IS NOT NULL FROM outbox_events WHERE action='create'")
    expiry = await conn.fetchval("SELECT available_at FROM outbox_events WHERE action='expire'")
    assert expiry == result.expires_at


async def test_ack_failure_rolls_back_result_and_recovers(vpn_database, core):
    conn, _ = vpn_database
    handlers, worker, panel = core
    user = await handlers.register.handle(RegisterUserRequest("a@example.com", "Alice"))
    await conn.execute("""CREATE FUNCTION reject_ack() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN IF NEW.published_at IS NOT NULL THEN RAISE EXCEPTION 'ack failure'; END IF; RETURN NEW; END $$;
    CREATE CONSTRAINT TRIGGER reject_ack AFTER UPDATE ON outbox_events DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION reject_ack();""")
    from app.domain.errors.adapters import UoWError
    with pytest.raises((asyncpg.RaiseError, UoWError)):
        await handlers.create.handle(CreateSubscriptionRequest(user.id, "create-one"))
    assert len(panel.users) == 1
    assert await conn.fetchval("SELECT external_id FROM vpn_subscriptions") is None
    assert await conn.fetchval("SELECT published_at FROM outbox_events WHERE action='create'") is None
    await conn.execute("DROP TRIGGER reject_ack ON outbox_events")
    assert await worker.process_one()
    assert await conn.fetchval("SELECT external_id FROM vpn_subscriptions") == 1
    assert len(panel.calls) == 1


async def test_cancellation_releases_user_and_event_locks(vpn_database, core):
    conn, _ = vpn_database
    handlers, worker, panel = core
    user = await handlers.register.handle(RegisterUserRequest("a@example.com", "Alice"))
    panel.started, panel.release = asyncio.Event(), asyncio.Event()
    task = asyncio.create_task(handlers.create.handle(CreateSubscriptionRequest(user.id, "create-one")))
    await panel.started.wait()
    assert not await worker.process_one()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    panel.started = None
    assert await worker.process_one()
    assert await conn.fetchval("SELECT external_id FROM vpn_subscriptions") == 1


async def test_old_expiry_is_superseded_after_renewal(vpn_database, core):
    conn, _ = vpn_database
    handlers, worker, panel = core
    user = await handlers.register.handle(RegisterUserRequest("a@example.com", "Alice"))
    await handlers.create.handle(CreateSubscriptionRequest(user.id, "create-one"))
    await handlers.extend.handle(ExtendSubscriptionRequest(user.id, "renew-one"))
    await conn.execute("UPDATE outbox_events SET available_at=now() WHERE action='expire' AND version=1")
    assert await worker.process_one()
    old = await conn.fetchrow("SELECT published_at,superseded_at FROM outbox_events WHERE action='expire' AND version=1")
    assert old[0] is None and old[1] is not None
    assert len(panel.calls) == 2


async def test_failed_delivery_keeps_durable_retry_and_safe_error(vpn_database, core):
    conn, _ = vpn_database
    handlers, _, panel = core
    user = await handlers.register.handle(RegisterUserRequest("a@example.com", "Alice"))
    panel.fail = True
    from app.domain.errors.vpn_user import PanelUnavailableError
    with pytest.raises(PanelUnavailableError):
        await handlers.create.handle(CreateSubscriptionRequest(user.id, "create-one"))
    event = await conn.fetchrow("SELECT attempts,last_error,published_at,available_at,created_at FROM outbox_events")
    assert event[0] > 0
    assert event[1] == "panel_unavailable"
    assert event[2] is None and event[3] > event[4]


async def test_two_workers_serialize_same_user_but_process_other_user(vpn_database, core):
    conn, pool = vpn_database
    handlers, worker, panel = core
    first = await handlers.register.handle(RegisterUserRequest("a@example.com", "Alice"))
    second = await handlers.register.handle(RegisterUserRequest("b@example.com", "Bob"))
    panel.fail = True
    from app.domain.errors.vpn_user import PanelUnavailableError
    for user in (first, second):
        with pytest.raises(PanelUnavailableError):
            await handlers.create.handle(CreateSubscriptionRequest(user.id, str(user.id)))
    await conn.execute("UPDATE outbox_events SET available_at=now()")
    panel.fail = False
    panel.started, panel.release = asyncio.Event(), asyncio.Event()
    task = asyncio.create_task(worker.process_one())
    await panel.started.wait()
    panel.started = None
    assert await worker.process_one()
    panel.release.set()
    assert await task
    assert await conn.fetchval("SELECT count(*) FROM vpn_subscriptions WHERE external_id IS NOT NULL") == 2


async def test_concurrent_renewals_preserve_both_weeks(vpn_database, core):
    conn, pool = vpn_database
    handlers, worker, panel = core
    user = await handlers.register.handle(RegisterUserRequest('a@example.com', 'Alice'))
    created = await handlers.create.handle(CreateSubscriptionRequest(user.id, 'create-one'))
    results = await asyncio.gather(handlers.extend.handle(ExtendSubscriptionRequest(user.id, 'renew-1')),
                                   handlers.extend.handle(ExtendSubscriptionRequest(user.id, 'renew-2')))
    assert len(results) == 2
    assert await conn.fetchval('SELECT expires_at FROM vpn_subscriptions') == created.expires_at + timedelta(days=14)
    assert len(panel.calls) == 3


async def test_concurrent_same_key_create_returns_one_result(vpn_database, core):
    _, pool = vpn_database
    handlers, worker, panel = core
    user = await handlers.register.handle(RegisterUserRequest('a@example.com', 'Alice'))
    first, second = await asyncio.gather(handlers.create.handle(CreateSubscriptionRequest(user.id, 'same-key')),
                                         handlers.create.handle(CreateSubscriptionRequest(user.id, 'same-key')))
    assert first == second
    assert len(panel.calls) == 1


async def test_duplicate_registration_normalizes_email(core):
    from app.domain.errors.vpn_user import VpnUserAlreadyExistsError
    handlers, _, _ = core
    await handlers.register.handle(RegisterUserRequest('a@example.com', 'Alice'))
    with pytest.raises(VpnUserAlreadyExistsError):
        await handlers.register.handle(RegisterUserRequest(' A@EXAMPLE.COM ', 'Other'))


async def test_expiry_disables_and_expired_renewal_reenables_from_now(core, monkeypatch):
    from app.services import vpn_outbox
    from app.services.use_cases.subscriptions.extend import handler as extend_handler
    handlers, worker, panel = core
    user = await handlers.register.handle(RegisterUserRequest('a@example.com', 'Alice'))
    created = await handlers.create.handle(CreateSubscriptionRequest(user.id, 'create-one'))
    now = created.expires_at + timedelta(days=2)
    monkeypatch.setattr(extend_handler, 'get_current_datetime', lambda: now)
    monkeypatch.setattr(vpn_outbox, 'get_current_datetime', lambda: now)
    assert await worker.process_one()
    disabled = await handlers.get.handle(GetSubscriptionRequest(user.id))
    assert disabled.status == 'disabled'
    renewed = await handlers.extend.handle(ExtendSubscriptionRequest(user.id, 'renew-one'))
    assert renewed.status == 'active'
    assert renewed.expires_at == now + timedelta(days=7)
    assert panel.calls[-1][-1] == 'ACTIVE'


async def test_cache_invalidation_failure_keeps_intent_and_prevents_success(vpn_database, core):
    from app.domain.errors.vpn_user import CacheUnavailableError
    conn, _ = vpn_database
    handlers, worker, panel = core
    user = await handlers.register.handle(RegisterUserRequest('a@example.com', 'Alice'))
    class FailedInvalidation(NoCache):
        async def invalidate(self, guid):
            raise CacheUnavailableError()
    worker._cache = FailedInvalidation()
    with pytest.raises(CacheUnavailableError):
        await handlers.create.handle(CreateSubscriptionRequest(user.id, 'create-one'))
    assert panel.calls == []
    assert await conn.fetchval("SELECT last_error FROM outbox_events WHERE action='create'") == 'cache_unavailable'
    worker._cache = NoCache()
    await handlers.create.handle(CreateSubscriptionRequest(user.id, 'create-one'))
    assert await conn.fetchval('SELECT external_id FROM vpn_subscriptions WHERE user_guid=$1', user.id) == 1


async def test_synchronous_budget_covers_all_predecessor_panel_operations(vpn_database, core):
    from app.domain.errors.vpn_user import PanelUnavailableError
    conn, _ = vpn_database
    handlers, worker, panel = core
    user = await handlers.register.handle(RegisterUserRequest('a@example.com', 'Alice'))
    await handlers.create.handle(CreateSubscriptionRequest(user.id, 'create-one'))
    panel.fail = True
    for key in ('renew-one', 'renew-two'):
        with pytest.raises(PanelUnavailableError):
            await handlers.extend.handle(ExtendSubscriptionRequest(user.id, key))
    panel.fail = False
    original_update = panel.update
    async def slow_update(subscription):
        await asyncio.sleep(0.06)
        return await original_update(subscription)
    panel.update = slow_update
    event_guid = await conn.fetchval("SELECT guid FROM outbox_events WHERE request_id='renew-two'")
    with pytest.raises(PanelUnavailableError):
        await worker.process_operation(event_guid)


async def test_cancel_after_remote_success_reconciles_on_restart(vpn_database, core):
    conn, _ = vpn_database
    handlers, worker, panel = core
    user = await handlers.register.handle(RegisterUserRequest('a@example.com', 'Alice'))
    original_create = panel.create
    remote_created = asyncio.Event()
    async def create_then_wait(subscription, expires_at):
        result = await original_create(subscription, expires_at)
        remote_created.set()
        await asyncio.Event().wait()
        return result
    panel.create = create_then_wait
    task = asyncio.create_task(handlers.create.handle(CreateSubscriptionRequest(user.id, 'create-one')))
    await remote_created.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await conn.fetchval('SELECT external_id FROM vpn_subscriptions') is None
    assert len(panel.users) == 1
    panel.create = original_create
    assert await worker.process_one()
    assert len(panel.calls) == 1
    assert await conn.fetchval('SELECT external_id FROM vpn_subscriptions') == 1


async def test_renewal_waits_for_inflight_expiry_then_reenables(core, monkeypatch):
    from app.services import vpn_outbox
    from app.services.use_cases.subscriptions.extend import handler as extend_handler
    handlers, worker, panel = core
    user = await handlers.register.handle(RegisterUserRequest('a@example.com', 'Alice'))
    created = await handlers.create.handle(CreateSubscriptionRequest(user.id, 'create-one'))
    now = created.expires_at + timedelta(seconds=1)
    monkeypatch.setattr(extend_handler, 'get_current_datetime', lambda: now)
    monkeypatch.setattr(vpn_outbox, 'get_current_datetime', lambda: now)
    disabling, release = asyncio.Event(), asyncio.Event()
    original_update = panel.update
    async def slow_disable(subscription):
        if subscription.desired_status == 'DISABLED':
            disabling.set()
            await release.wait()
        return await original_update(subscription)
    panel.update = slow_disable
    expiry = asyncio.create_task(worker.process_one())
    await disabling.wait()
    renewal = asyncio.create_task(handlers.extend.handle(ExtendSubscriptionRequest(user.id, 'renew-one')))
    await asyncio.sleep(0)
    assert not renewal.done()
    release.set()
    assert await expiry
    result = await renewal
    assert result.status == 'active' and result.expires_at == now + timedelta(days=7)
    assert panel.calls[-1][-1] == 'ACTIVE'


async def test_same_version_expiry_waits_until_renewal_recovery_settles(core, vpn_database, monkeypatch):
    from app.services import vpn_outbox
    handlers, worker, panel = core
    conn, _ = vpn_database
    user = await handlers.register.handle(RegisterUserRequest('ordering@example.com', 'Ordering'))
    created = await handlers.create.handle(CreateSubscriptionRequest(user.id, 'ordering-create'))
    panel.fail = True
    from app.domain.errors.vpn_user import PanelUnavailableError
    with pytest.raises(PanelUnavailableError):
        await handlers.extend.handle(ExtendSubscriptionRequest(user.id, 'ordering-renew'))
    target = created.expires_at + timedelta(days=7)
    now = target + timedelta(seconds=1)
    monkeypatch.setattr(vpn_outbox, 'get_current_datetime', lambda: now)
    await conn.execute("UPDATE outbox_events SET available_at=$1 WHERE action='renew'", now + timedelta(seconds=30))
    panel.fail = False
    assert await worker.process_one()
    assert not await worker.process_one()
    assert await conn.fetchval("SELECT published_at FROM outbox_events WHERE action='expire' AND version=2") is None
    await conn.execute("UPDATE outbox_events SET available_at=$1 WHERE action='renew'", now)
    while await worker.process_one():
        pass
    final = await handlers.get.handle(GetSubscriptionRequest(user.id))
    assert final.status == 'disabled'
    assert panel.calls[-1][-1] == 'DISABLED'
    assert await conn.fetchval("SELECT count(*) FROM outbox_events WHERE published_at IS NULL AND superseded_at IS NULL") == 0


@pytest.mark.parametrize('lock_kind', ['row', 'advisory'])
async def test_mutation_deadline_bounds_initial_database_locks(core, vpn_database, lock_kind):
    from app.domain.errors.vpn_user import PanelUnavailableError
    handlers, _, panel = core
    conn, pool = vpn_database
    user = await handlers.register.handle(RegisterUserRequest('budget@example.com', 'Budget'))
    key = 'budget-key'
    async with pool.acquire() as blocker:
        transaction = blocker.transaction()
        await transaction.start()
        if lock_kind == 'row':
            await blocker.execute('SELECT guid FROM vpn_users WHERE guid=$1 FOR UPDATE', user.id)
        else:
            await blocker.execute('SELECT pg_advisory_xact_lock(hashtextextended($1,0))', key)
        try:
            started = asyncio.get_running_loop().time()
            with pytest.raises(PanelUnavailableError):
                await asyncio.wait_for(handlers.create.handle(CreateSubscriptionRequest(user.id, key)), timeout=0.14)
            assert asyncio.get_running_loop().time() - started < 0.14
            assert panel.calls == []
        finally:
            await transaction.rollback()
    assert await conn.fetchval('SELECT count(*) FROM vpn_subscriptions') == 0
    await handlers.create.handle(CreateSubscriptionRequest(user.id, key))
    assert await conn.fetchval('SELECT external_id FROM vpn_subscriptions WHERE user_guid=$1', user.id) == 1


@pytest.mark.parametrize('phase', ['worker_factory', 'cache_invalidation'])
async def test_mutation_deadline_keeps_intent_after_delayed_delivery_setup(core, vpn_database, phase):
    from app.domain.errors.vpn_user import PanelUnavailableError
    handlers, worker, panel = core
    conn, _ = vpn_database
    user = await handlers.register.handle(RegisterUserRequest('budget@example.com', 'Budget'))
    old_factory, old_cache = worker._uow_factory, worker._cache
    closed = asyncio.Event()
    if phase == 'worker_factory':
        @asynccontextmanager
        async def delayed_factory():
            try:
                await asyncio.Event().wait()
                async with old_factory() as uow:
                    yield uow
            finally:
                closed.set()
        worker._uow_factory = delayed_factory
    else:
        class DelayedInvalidation(NoCache):
            async def invalidate(self, guid):
                try:
                    await asyncio.Event().wait()
                finally:
                    closed.set()
        worker._cache = DelayedInvalidation()
    try:
        with pytest.raises(PanelUnavailableError):
            await asyncio.wait_for(handlers.create.handle(CreateSubscriptionRequest(user.id, 'budget-key')), timeout=0.14)
        assert closed.is_set()
        assert panel.calls == []
        assert await conn.fetchval("SELECT published_at FROM outbox_events WHERE action='create'") is None
        assert await conn.fetchval('SELECT count(*) FROM vpn_subscriptions') == 1
    finally:
        worker._uow_factory, worker._cache = old_factory, old_cache
    await handlers.create.handle(CreateSubscriptionRequest(user.id, 'budget-key'))
    assert await conn.fetchval('SELECT external_id FROM vpn_subscriptions WHERE user_guid=$1', user.id) == 1


@pytest.mark.parametrize('mismatch', ['expiry', 'status'])
async def test_unconfirmed_patch_keeps_target_and_unpublished_intent(core, vpn_database, mismatch):
    import json

    import httpx

    from app.domain.errors.vpn_user import PanelUnavailableError
    from app.infrastructure.adapters.vpn.remnawave import RemnawavePanel
    handlers, worker, _ = core
    conn, _ = vpn_database
    user = await handlers.register.handle(RegisterUserRequest('mismatch@example.com', 'Mismatch'))
    created = await handlers.create.handle(CreateSubscriptionRequest(user.id, 'create-one'))
    external_id = await conn.fetchval('SELECT external_id FROM vpn_subscriptions WHERE user_guid=$1', user.id)
    async def handler(request):
        body = json.loads(request.content)
        return httpx.Response(200, json={'response': {
            'id': external_id, 'username': 'vpn_' + user.id.hex,
            'description': 'asyncpytemplate:' + str(user.id),
            'subscriptionUrl': created.subscription_url,
            'expireAt': created.expires_at.isoformat() if mismatch == 'expiry' else body['expireAt'],
            'status': 'DISABLED' if mismatch == 'status' else 'ACTIVE',
        }})
    async with httpx.AsyncClient(base_url='https://panel.invalid', transport=httpx.MockTransport(handler)) as client:
        worker._panel = RemnawavePanel(client)
        with pytest.raises(PanelUnavailableError):
            await handlers.extend.handle(ExtendSubscriptionRequest(user.id, 'renew-one'))
    state = await conn.fetchrow('SELECT expires_at,desired_expires_at FROM vpn_subscriptions')
    assert state['expires_at'] == created.expires_at
    assert state['desired_expires_at'] == created.expires_at + timedelta(days=7)
    event = await conn.fetchrow("SELECT result,published_at FROM outbox_events WHERE action='renew'")
    assert event['result'] is None and event['published_at'] is None


async def test_mutation_deadline_bounds_initial_pool_acquisition(core, vpn_database):
    from app.domain.errors.vpn_user import PanelUnavailableError
    handlers, _, panel = core
    conn, pool = vpn_database
    user = await handlers.register.handle(RegisterUserRequest('budget@example.com', 'Budget'))
    held = [await pool.acquire() for _ in range(4)]
    try:
        with pytest.raises(PanelUnavailableError):
            await asyncio.wait_for(handlers.create.handle(CreateSubscriptionRequest(user.id, 'budget-key')), timeout=0.14)
        assert panel.calls == []
        assert await conn.fetchval('SELECT count(*) FROM vpn_subscriptions') == 0
    finally:
        for connection in held:
            await pool.release(connection)
    await handlers.create.handle(CreateSubscriptionRequest(user.id, 'budget-key'))
    assert await conn.fetchval('SELECT external_id FROM vpn_subscriptions WHERE user_guid=$1', user.id) == 1


@pytest.mark.parametrize('phase', ['result_read', 'result_write'])
async def test_mutation_deadline_covers_success_cache_without_losing_committed_result(core, vpn_database, phase):
    from app.domain.errors.vpn_user import PanelUnavailableError
    handlers, _, panel = core
    conn, _ = vpn_database
    user = await handlers.register.handle(RegisterUserRequest('budget@example.com', 'Budget'))
    if phase == 'result_read':
        await handlers.create.handle(CreateSubscriptionRequest(user.id, 'budget-key'))
    cleaned_up = asyncio.Event()
    class SlowSuccessCache(NoCache):
        async def get_result(self, key):
            try:
                await asyncio.Event().wait()
            finally:
                cleaned_up.set()
        async def set_result(self, key, result):
            try:
                await asyncio.Event().wait()
            finally:
                cleaned_up.set()
    handlers.mutation._cache = SlowSuccessCache()
    with pytest.raises(PanelUnavailableError):
        await asyncio.wait_for(handlers.create.handle(CreateSubscriptionRequest(user.id, 'budget-key')), timeout=0.14)
    assert cleaned_up.is_set()
    assert await conn.fetchval("SELECT published_at IS NOT NULL FROM outbox_events WHERE action='create'")
    handlers.mutation._cache = NoCache()
    await handlers.create.handle(CreateSubscriptionRequest(user.id, 'budget-key'))
    assert await conn.fetchval('SELECT external_id FROM vpn_subscriptions WHERE user_guid=$1', user.id) == 1
    assert len(panel.calls) == 1


async def test_intent_connection_is_released_before_worker_with_single_connection_pool(core, vpn_database, database_url):
    from app.domain.dto.vpn_user import VpnSettings
    from app.services.vpn_outbox import VpnOutboxWorker
    from app.services.use_cases.subscriptions.create.handler import CreateSubscriptionHandler
    from app.services.use_cases.subscriptions.mutation import SubscriptionMutation
    from app.services.use_cases.users.register.handler import RegisterUserHandler
    conn, _ = vpn_database
    schema = await conn.fetchval('SELECT current_schema()')
    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=1,
                                    server_settings={'search_path': schema})
    @asynccontextmanager
    async def factory():
        async with pool.acquire() as connection:
            yield make_uow(connection)
    _, _, panel = core
    settings = VpnSettings(operation_timeout=0.2)
    worker = VpnOutboxWorker(factory, panel, NoCache(), settings)
    mutation = SubscriptionMutation(factory, worker, NoCache(), settings)
    handlers = SimpleNamespace(register=RegisterUserHandler(factory), create=CreateSubscriptionHandler(mutation, settings))
    try:
        user = await handlers.register.handle(RegisterUserRequest('single@example.com', 'Single'))
        await handlers.create.handle(CreateSubscriptionRequest(user.id, 'single-key'))
        assert await conn.fetchval('SELECT external_id FROM vpn_subscriptions WHERE user_guid=$1', user.id) == 1
    finally:
        await pool.close()


async def test_past_target_http_recovery_acknowledges_expired_then_disables(core, vpn_database, monkeypatch):
    import json

    import httpx

    from app.domain.errors.vpn_user import PanelUnavailableError
    from app.infrastructure.adapters.vpn import remnawave
    from app.services import vpn_outbox
    handlers, worker, panel = core
    conn, _ = vpn_database
    user = await handlers.register.handle(RegisterUserRequest('past@example.com', 'Past'))
    created = await handlers.create.handle(CreateSubscriptionRequest(user.id, 'create-one'))
    external_id = await conn.fetchval('SELECT external_id FROM vpn_subscriptions WHERE user_guid=$1', user.id)
    panel.fail = True
    with pytest.raises(PanelUnavailableError):
        await handlers.extend.handle(ExtendSubscriptionRequest(user.id, 'renew-one'))
    target = created.expires_at + timedelta(days=7)
    now = target + timedelta(seconds=1)
    monkeypatch.setattr(vpn_outbox, 'get_current_datetime', lambda: now)
    monkeypatch.setattr(remnawave, 'get_current_datetime', lambda: now)
    await conn.execute("UPDATE outbox_events SET available_at=$1 WHERE action='renew'", now)
    statuses = []
    remote_expiry = created.expires_at.isoformat()
    async def handler(request):
        nonlocal remote_expiry
        body = json.loads(request.content)
        statuses.append(body['status'])
        if body['status'] == 'ACTIVE':
            remote_expiry = body['expireAt']
        return httpx.Response(200, json={'response': {
            'id': external_id, 'username': 'vpn_' + user.id.hex,
            'description': 'asyncpytemplate:' + str(user.id),
            'subscriptionUrl': created.subscription_url, 'expireAt': remote_expiry,
            'status': 'EXPIRED' if body['status'] == 'ACTIVE' else 'DISABLED',
        }})
    async with httpx.AsyncClient(base_url='https://panel.invalid', transport=httpx.MockTransport(handler)) as client:
        worker._panel = remnawave.RemnawavePanel(client)
        while await worker.process_one():
            pass
    final = await handlers.get.handle(GetSubscriptionRequest(user.id))
    assert final.status == 'disabled' and final.expires_at == target
    assert statuses == ['ACTIVE', 'DISABLED']
    assert await conn.fetchval("SELECT count(*) FROM outbox_events WHERE published_at IS NULL AND superseded_at IS NULL") == 0


async def test_slow_background_panel_commits_retry_and_allows_next_user(core, vpn_database):
    from app.domain.errors.vpn_user import PanelUnavailableError
    handlers, worker, panel = core
    conn, _ = vpn_database
    first = await handlers.register.handle(RegisterUserRequest('slow@example.com', 'Slow'))
    second = await handlers.register.handle(RegisterUserRequest('fast@example.com', 'Fast'))
    panel.fail = True
    for user in (first, second):
        with pytest.raises(PanelUnavailableError):
            await handlers.create.handle(CreateSubscriptionRequest(user.id, str(user.id)))
    await conn.execute('UPDATE outbox_events SET available_at=now()')
    panel.fail = False
    original_create = panel.create
    async def slow_first(subscription, expires_at):
        if subscription.user_guid == first.id:
            await asyncio.Event().wait()
        return await original_create(subscription, expires_at)
    panel.create = slow_first
    assert await worker.process_one()
    pending = await conn.fetchrow("SELECT attempts,last_error,published_at,available_at FROM outbox_events WHERE user_guid=$1", first.id)
    assert pending['attempts'] >= 2 and pending['last_error'] == 'panel_unavailable'
    assert pending['published_at'] is None
    assert await worker.process_one()
    assert await conn.fetchval('SELECT external_id FROM vpn_subscriptions WHERE user_guid=$1', second.id) == 1


@pytest.mark.parametrize('method,request_type', [('create', CreateSubscriptionRequest), ('extend', ExtendSubscriptionRequest)])
async def test_unknown_user_404_precedes_existing_idempotency_key(core, method, request_type):
    from app.domain.errors.vpn_user import (
        IdempotencyConflictError,
        VpnUserNotFoundError,
    )
    handlers, _, _ = core
    user = await handlers.register.handle(RegisterUserRequest('known@example.com', 'Known'))
    await handlers.create.handle(CreateSubscriptionRequest(user.id, 'used-key'))
    with pytest.raises(VpnUserNotFoundError):
        await getattr(handlers, method).handle(request_type(uuid4(), 'used-key'))
    other = await handlers.register.handle(RegisterUserRequest('other@example.com', 'Other'))
    with pytest.raises(IdempotencyConflictError):
        await getattr(handlers, method).handle(request_type(other.id, 'used-key'))
    with pytest.raises(IdempotencyConflictError):
        await handlers.extend.handle(ExtendSubscriptionRequest(user.id, 'used-key'))


async def test_recurring_reconciliation_repairs_late_renewal_without_rewriting_results(core, vpn_database):
    handlers, worker, panel = core
    conn, _ = vpn_database
    user = await handlers.register.handle(RegisterUserRequest('delayed@example.com', 'Delayed'))
    await handlers.create.handle(CreateSubscriptionRequest(user.id, 'create'))
    original_update = panel.update
    release_old = asyncio.Event()
    late_request = None
    first = True
    async def update(subscription):
        nonlocal first, late_request
        if first:
            first = False
            async def finish_later():
                await release_old.wait()
                return await original_update(subscription)
            late_request = asyncio.create_task(finish_later())
            raise TimeoutError('accepted remote request still running')
        return await original_update(subscription)
    panel.update = update
    try:
        first_result = await handlers.extend.handle(ExtendSubscriptionRequest(user.id, 'renew-one'))
        latest = await handlers.extend.handle(ExtendSubscriptionRequest(user.id, 'renew-two'))
        history = await conn.fetch('SELECT guid,result,published_at FROM outbox_events WHERE result IS NOT NULL ORDER BY guid')
        await worker.reconcile(asyncio.Event())
        release_old.set()
        await late_request
        assert panel.users[user.id].expires_at == first_result.expires_at
        assert not await worker.process_one()
        await worker.reconcile(asyncio.Event())
        assert panel.users[user.id].expires_at == latest.expires_at
        assert panel.users[user.id].status == 'ACTIVE'
        assert await conn.fetch('SELECT guid,result,published_at FROM outbox_events WHERE result IS NOT NULL ORDER BY guid') == history
        assert await handlers.extend.handle(ExtendSubscriptionRequest(user.id, 'renew-one')) == first_result
        assert await handlers.extend.handle(ExtendSubscriptionRequest(user.id, 'renew-two')) == latest
        assert await conn.fetchval("SELECT count(*) FROM outbox_events WHERE action='renew'") == 2
    finally:
        release_old.set()
        if late_request:
            await late_request


async def test_reconciliation_repairs_late_disable_after_renewal(core, monkeypatch):
    from app.services import vpn_outbox
    from app.services.use_cases.subscriptions.extend import handler as extend_handler
    handlers, worker, panel = core
    user = await handlers.register.handle(RegisterUserRequest('disabled@example.com', 'Disabled'))
    created = await handlers.create.handle(CreateSubscriptionRequest(user.id, 'create'))
    now = created.expires_at + timedelta(seconds=1)
    monkeypatch.setattr(vpn_outbox, 'get_current_datetime', lambda: now)
    monkeypatch.setattr(extend_handler, 'get_current_datetime', lambda: now)
    original_update = panel.update
    release_old = asyncio.Event()
    late_request = None
    first = True
    async def update(subscription):
        nonlocal first, late_request
        if subscription.desired_status == 'DISABLED' and first:
            first = False
            async def finish_later():
                await release_old.wait()
                return await original_update(subscription)
            late_request = asyncio.create_task(finish_later())
            raise TimeoutError('disable still running remotely')
        return await original_update(subscription)
    panel.update = update
    try:
        assert await worker.process_one()
        latest = await handlers.extend.handle(ExtendSubscriptionRequest(user.id, 'renew'))
        release_old.set()
        await late_request
        assert panel.users[user.id].status == 'DISABLED'
        await worker.reconcile(asyncio.Event())
        assert panel.users[user.id].status == 'ACTIVE'
        assert panel.users[user.id].expires_at == latest.expires_at
    finally:
        release_old.set()
        if late_request:
            await late_request


async def test_expired_reconciliation_never_resurrects_and_failure_retries_next_cycle(core, vpn_database, monkeypatch):
    from msgspec.structs import replace

    from app.services import vpn_outbox
    handlers, worker, panel = core
    conn, _ = vpn_database
    user = await handlers.register.handle(RegisterUserRequest('expired@example.com', 'Expired'))
    created = await handlers.create.handle(CreateSubscriptionRequest(user.id, 'create'))
    now = created.expires_at + timedelta(seconds=1)
    monkeypatch.setattr(vpn_outbox, 'get_current_datetime', lambda: now)
    assert await worker.process_one()
    panel.users[user.id] = replace(panel.users[user.id], status='ACTIVE')
    panel.fail = True
    await worker.reconcile(asyncio.Event())
    assert panel.users[user.id].status == 'ACTIVE'
    panel.fail = False
    await worker.reconcile(asyncio.Event())
    assert panel.users[user.id].status == 'DISABLED'
    assert panel.users[user.id].expires_at == created.expires_at
    assert await conn.fetchval('SELECT desired_expires_at FROM vpn_subscriptions') == created.expires_at
    assert panel.calls[-1][-1] == 'DISABLED'


async def test_reconciliation_visits_future_subscriptions_once_per_cycle_and_obeys_stop(core):
    from msgspec.structs import replace
    handlers, worker, panel = core
    users = []
    for index in range(3):
        user = await handlers.register.handle(RegisterUserRequest(f'cycle{index}@example.com', 'Cycle'))
        users.append(user)
        await handlers.create.handle(CreateSubscriptionRequest(user.id, f'create-{index}'))
        panel.users[user.id] = replace(panel.users[user.id], status='DISABLED')
    visited = []
    stop = asyncio.Event()
    stop_after_first = True
    original_reconcile = panel.reconcile
    async def observe(subscription):
        visited.append(subscription.user_guid)
        result = await original_reconcile(subscription)
        if stop_after_first:
            stop.set()
        return result
    panel.reconcile = observe
    await worker.reconcile(stop)
    assert len(visited) == 1
    assert sum(value.status == 'ACTIVE' for value in panel.users.values()) == 1
    stop_after_first = False
    visited.clear()
    stop.clear()
    await worker.reconcile(stop)
    assert len(visited) == 3 and set(visited) == {user.id for user in users}
    assert all(value.status == 'ACTIVE' for value in panel.users.values())
