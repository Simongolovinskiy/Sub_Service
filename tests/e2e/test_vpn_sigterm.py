import asyncio
import os
import signal
import socket
import sys
from datetime import datetime, timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import UUID

import httpx
import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from tests.integration import test_vpn_cache, test_vpn_core

vpn_database = test_vpn_core.vpn_database
redis_url = test_vpn_cache.redis_url
pytestmark = pytest.mark.e2e


async def test_production_launcher_sigterm_preserves_intent_and_recovers(vpn_database, database_url, redis_url, tmp_path):
    conn, pool = vpn_database
    schema = await conn.fetchval('SELECT current_schema()')
    parts = urlsplit(database_url)
    dsn = urlunsplit(parts._replace(query=urlencode([*parse_qsl(parts.query), ('search_path', schema)])))
    remote = {}
    entered = asyncio.Event()
    release = asyncio.Event()
    blocked = 'create'
    panel = FastAPI()

    @panel.get('/api/users/by-username/{username}')
    async def lookup(username: str):
        if username not in remote:
            return JSONResponse({}, status_code=404)
        return {'response': remote[username]}

    @panel.post('/api/users', status_code=201)
    async def create(request: Request):
        body = await request.json()
        assert request.headers['authorization'] == 'Bearer local-test-token'
        assert body['username'] not in remote
        remote[body['username']] = {
            **body, 'id': len(remote) + 1, 'subscriptionUrl': 'https://local.invalid/subscription',
            'privateKey': 'panel-private-field',
        }
        if blocked == 'create':
            entered.set()
            await release.wait()
        return {'response': remote[body['username']]}

    @panel.patch('/api/users')
    async def update(request: Request):
        body = await request.json()
        account = next(value for value in remote.values() if value['id'] == body['id'])
        account.update(body)
        if blocked == 'renew':
            entered.set()
            await release.wait()
        return {'response': account}

    panel_socket = socket.socket()
    panel_socket.bind(('127.0.0.1', 0))
    panel_port = panel_socket.getsockname()[1]
    panel_server = uvicorn.Server(uvicorn.Config(panel, log_config=None, access_log=False, lifespan='off'))
    panel_task = asyncio.create_task(panel_server.serve(sockets=[panel_socket]))
    while not panel_server.started:
        await asyncio.sleep(0.01)
    processes = []
    log_files = []

    async def launch():
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        env = {**os.environ, 'DATABASE_CONNECTION_STRING': dsn, 'APP_WORKERS': '1', 'APP_PORT': str(port),
               'VPN_REDIS_URL': redis_url, 'VPN_PANEL_BASE_URL': f'http://127.0.0.1:{panel_port}',
               'VPN_PANEL_TOKEN': 'local-test-token', 'VPN_POLLING_SECONDS': '0.1',
               'VPN_OPERATION_TIMEOUT': '2', 'LOG_LEVEL': 'INFO'}
        log_path = tmp_path / f'launcher-{len(processes)}.log'
        log_file = log_path.open('wb')
        log_files.append((log_file, log_path))
        process = await asyncio.create_subprocess_exec(sys.executable, '-m', 'app', env=env,
                                                       stdout=log_file, stderr=asyncio.subprocess.STDOUT)
        processes.append(process)
        client = httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}', timeout=4, trust_env=False)
        for _ in range(200):
            assert process.returncode is None, log_path.read_text()
            try:
                if (await client.get('/healthz')).status_code == 200:
                    return process, client
            except httpx.RequestError:
                pass
            await asyncio.sleep(0.02)
        pytest.fail('Production launcher did not become ready')

    async def terminate(process):
        start = asyncio.get_running_loop().time()
        process.send_signal(signal.SIGTERM)
        await asyncio.wait_for(process.wait(), timeout=8)
        elapsed = asyncio.get_running_loop().time() - start
        assert process.returncode in (0, -signal.SIGTERM)
        assert 0.45 < elapsed < 1.5, elapsed
        return elapsed

    clients = []
    try:
        process, client = await launch()
        clients.append(client)
        response = await client.post('/users', json={'email': 'sigterm@example.com', 'name': 'Signal test'})
        assert response.status_code == 201
        user = response.json()['id']
        pending = asyncio.create_task(client.post(f'/users/{user}/subscription', headers={'Idempotency-Key': 'sigterm-create'}))
        await asyncio.wait_for(entered.wait(), timeout=3)
        create_expiry = datetime.fromisoformat(next(iter(remote.values()))['expireAt'])
        await terminate(process)
        await asyncio.gather(pending, return_exceptions=True)
        assert await conn.fetchval("SELECT published_at IS NULL FROM outbox_events WHERE request_id='sigterm-create'")
        async with conn.transaction():
            await conn.execute("SET LOCAL lock_timeout='250ms'")
            await conn.fetchrow('SELECT * FROM vpn_users WHERE guid=$1 FOR UPDATE', UUID(user))
            await conn.fetchrow("SELECT * FROM outbox_events WHERE request_id='sigterm-create' FOR UPDATE")
        assert len(remote) == 1
        release.set()
        blocked = None

        process, client = await launch()
        clients.append(client)
        created = await client.post(f'/users/{user}/subscription', headers={'Idempotency-Key': 'sigterm-create'})
        assert created.status_code == 201, created.text
        assert datetime.fromisoformat(created.json()['expires_at']) == create_expiry
        assert set(created.json()) == {'subscription_url', 'expires_at', 'status'}
        blocked = 'renew'
        entered.clear()
        release.clear()
        pending = asyncio.create_task(client.post(f'/users/{user}/subscription/extend', headers={'Idempotency-Key': 'sigterm-renew'}))
        await asyncio.wait_for(entered.wait(), timeout=3)
        await terminate(process)
        await asyncio.gather(pending, return_exceptions=True)
        assert await conn.fetchval("SELECT published_at IS NULL FROM outbox_events WHERE request_id='sigterm-renew'")
        target = await conn.fetchval('SELECT desired_expires_at FROM vpn_subscriptions')
        assert target == create_expiry + timedelta(days=7)
        async with conn.transaction():
            await conn.execute("SET LOCAL lock_timeout='250ms'")
            await conn.fetchrow('SELECT * FROM vpn_subscriptions FOR UPDATE')
        release.set()
        blocked = None

        process, client = await launch()
        clients.append(client)
        renewed = await client.post(f'/users/{user}/subscription/extend', headers={'Idempotency-Key': 'sigterm-renew'})
        assert renewed.status_code == 200, renewed.text
        assert datetime.fromisoformat(renewed.json()['expires_at']) == target
        assert len(remote) == 1
        assert datetime.fromisoformat(next(iter(remote.values()))['expireAt']) == target
        assert await conn.fetchval("SELECT count(*) FROM outbox_events WHERE action IN ('create','renew') AND published_at IS NULL") == 0
        process.send_signal(signal.SIGTERM)
        await asyncio.wait_for(process.wait(), timeout=8)
        logs = '\n'.join(path.read_text() for _, path in log_files)
        assert 'timeout graceful shutdown exceeded' in logs.lower()
        assert 'Application stopped gracefully' in logs
        assert 'local-test-token' not in logs
        assert 'panel-private-field' not in logs
        assert 'Task was destroyed' not in logs
        assert 'Database pool closed' in logs
    finally:
        release.set()
        for client in clients:
            await client.aclose()
        for process in processes:
            if process.returncode is None:
                process.send_signal(signal.SIGTERM)
                try:
                    await asyncio.wait_for(process.wait(), timeout=8)
                except TimeoutError:
                    process.kill()
                    await process.wait()
        for file, _ in log_files:
            file.close()
        panel_server.should_exit = True
        await panel_task
