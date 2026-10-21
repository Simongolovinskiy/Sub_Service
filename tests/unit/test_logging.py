import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest


def run_logging(source, **env):
    with TemporaryDirectory() as directory:
        script = Path(directory) / 'logging_probe.py'
        script.write_text(textwrap.dedent(source))
        result = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True, text=True,
            env={**os.environ, **env, 'PYTHONPATH': os.getcwd()}, timeout=20,
        )
    assert result.returncode == 0, result.stderr
    assert result.stderr == ''
    assert '\x1b' not in result.stdout
    return [json.loads(line) for line in result.stdout.splitlines()]


def test_stdout_json_context_stdlib_and_exception():
    records = run_logging('''
        import logging
        from app.infrastructure.adapters._logging import get_logger
        from app.infrastructure.adapters._logging.context import set_request_id
        logger = get_logger()
        get_logger()
        set_request_id('request-test')
        logger.info('direct', customer=42)
        logging.warning('stdlib', extra={'job_id': 'job-test'})
        logging.log(25, 'custom-level')
        try:
            private_local = 'DO_NOT_LEAK_LOCAL'
            raise ValueError('expected failure') if private_local else None
        except ValueError:
            logging.exception('exception-test')
        logger.complete()
    ''', LOG_LEVEL='INFO')
    from app.domain.common.constants import SERVICE_NAME
    messages = [item['record']['message'] for item in records]
    for message in ('direct', 'stdlib', 'custom-level', 'exception-test'):
        assert messages.count(message) == 1
    selected = {item['record']['message']: item for item in records}
    assert selected['direct']['record']['extra']['customer'] == 42
    assert selected['stdlib']['record']['extra']['job_id'] == 'job-test'
    for message in ('direct', 'stdlib', 'custom-level', 'exception-test'):
        assert selected[message]['record']['extra']['service'] == SERVICE_NAME
        assert selected[message]['record']['extra']['request_id'] == 'request-test'
    assert selected['custom-level']['record']['level']['no'] == 25
    assert selected['stdlib']['record']['function'] == '<module>'
    exception = selected['exception-test']
    assert exception['record']['exception']['type'] == 'ValueError'
    assert 'Traceback' in exception['text']
    assert 'DO_NOT_LEAK_LOCAL' not in exception['text']


def test_level_reads_environment_at_initialization():
    records = run_logging('''
        import os
        import logging
        from app.infrastructure.adapters._logging import get_logger
        os.environ['LOG_LEVEL'] = 'WARNING'
        logger = get_logger()
        logger.info('hidden-direct')
        logging.info('hidden-stdlib')
        logger.warning('visible-direct')
        logging.warning('visible-stdlib')
        logger.complete()
    ''', LOG_LEVEL='INFO')
    assert [item['record']['message'] for item in records] == ['visible-direct', 'visible-stdlib']


def test_slow_stdout_writes_off_event_loop_thread():
    run_logging('''
        import asyncio
        import sys
        import threading
        from app.infrastructure.adapters._logging import get_logger
        entered = threading.Event()
        release = threading.Event()
        writer_threads = []
        original = sys.stdout
        class SlowStream:
            def write(self, message):
                writer_threads.append(threading.get_ident())
                entered.set()
                assert release.wait(5)
                original.write(message)
            def flush(self):
                original.flush()
        sys.stdout = SlowStream()
        async def main():
            loop_thread = threading.get_ident()
            logger = get_logger()
            try:
                logger.info('queued-final')
                assert await asyncio.to_thread(entered.wait, 2)
                await asyncio.sleep(0)
                assert all(thread != loop_thread for thread in writer_threads)
            finally:
                release.set()
            await logger.complete()
        asyncio.run(main())
    ''', LOG_LEVEL='INFO')


@pytest.mark.parametrize('close_fails', [False, True])
async def test_lifespan_completes_logging_after_container_close(monkeypatch, close_fails):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock

    from app.application import factory
    events = []
    async def close():
        events.append('close')
        if close_fails:
            raise RuntimeError('close failed')
    async def complete():
        events.append('complete')
    logger = Mock()
    logger.complete = AsyncMock(side_effect=complete)
    monkeypatch.setattr(factory, 'logger', logger)
    app = SimpleNamespace(state=SimpleNamespace(
        container=SimpleNamespace(close=close),
        runtime=SimpleNamespace(start=AsyncMock(), close=AsyncMock()),
    ))
    if close_fails:
        with pytest.raises(RuntimeError, match='close failed'):
            async with factory.lifespan(app):
                pass
    else:
        async with factory.lifespan(app):
            pass
    assert events == ['close', 'complete']


def test_generic_handler_preserves_exception():
    records = run_logging('''
        import asyncio
        from fastapi import FastAPI
        from starlette.requests import Request
        from app.application.factory import setup_exception_handlers, logger
        app = FastAPI()
        setup_exception_handlers(app)
        async def main():
            try:
                raise ValueError('handler-failure')
            except ValueError as exc:
                request = Request({'type': 'http', 'path': '/failure', 'headers': []})
                response = await app.exception_handlers[Exception](request, exc)
                assert response.status_code == 500
            await logger.complete()
        asyncio.run(main())
    ''', LOG_LEVEL='INFO')
    record = next(item for item in records if item['record']['message'] == 'Unexpected error occurred')
    assert record['record']['exception']['type'] == 'ValueError'
    assert 'handler-failure' in record['text']


def test_uvicorn_config_routes_its_loggers_once():
    records = run_logging('''
        import json
        import logging
        import logging.config
        from pathlib import Path
        from app.infrastructure.adapters._logging import get_logger
        config = Path('app/infrastructure/adapters/_logging/uvicorn.json')
        logging.config.dictConfig(json.loads(config.read_text()))
        logging.getLogger('uvicorn.error').info('server-marker')
        logging.getLogger('uvicorn.access').info('access-marker')
        logging.getLogger('uvicorn.asgi').info('asgi-marker')
        logging.info('root-marker')
        get_logger().complete()
    ''', LOG_LEVEL='INFO')
    assert [item['record']['message'] for item in records] == [
        'server-marker', 'access-marker', 'asgi-marker', 'root-marker',
    ]


def test_migrations_initialize_shared_logging_before_backend_work():
    records = run_logging('''
        import logging
        from app.infrastructure.adapters.persistence.rdb import migration_apply
        from app.infrastructure.adapters._logging import get_logger
        def backend(connection_string):
            logging.warning('migration-marker')
            raise RuntimeError('backend unavailable')
        migration_apply.get_backend = backend
        try:
            migration_apply.db_yoyo_migration('unused')
        except RuntimeError:
            pass
        get_logger().complete()
    ''', LOG_LEVEL='INFO')
    assert [item['record']['message'] for item in records] == ['migration-marker']


def test_spawn_process_exit_leaves_no_logging_semaphore_warning():
    import multiprocessing

    if 'fork' not in multiprocessing.get_all_start_methods():
        pytest.skip('Platform has no untracked fork-context semaphores')
    records = run_logging('''
        import multiprocessing
        import os
        multiprocessing.set_start_method('spawn')
        from app.infrastructure.adapters._logging import get_logger
        logger = get_logger()
        logger.info('before-process-exit')
        logger.complete()
        os._exit(0)
    ''', LOG_LEVEL='INFO')
    assert [item['record']['message'] for item in records] == ['before-process-exit']
