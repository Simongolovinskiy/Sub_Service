import asyncio
import signal
import threading
from datetime import UTC

from apscheduler.events import EVENT_SCHEDULER_SHUTDOWN
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dishka import AsyncContainer
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send
from uvicorn import Server

from app.infrastructure.adapters._logging import get_logger
from app.infrastructure.adapters.vpn.settings import VpnRuntimeSettings
from app.services.vpn_outbox import VpnOutboxWorker

logger = get_logger()


class VpnRuntime:
    def __init__(self):
        self.initialized = False
        self.stopping = asyncio.Event()
        self.http_tasks: set[asyncio.Task] = set()
        self.job_tasks: set[asyncio.Task] = set()
        self.scheduler = AsyncIOScheduler(timezone=UTC)
        self.worker = None
        self._signal_handlers = {}

    async def start(self, container: AsyncContainer) -> None:
        self.container = container
        settings = await container.get(VpnRuntimeSettings)
        self.scheduler.add_job(self.tick, 'interval', seconds=settings.polling_seconds,
                               coalesce=True, max_instances=1)
        self.scheduler.start()
        if threading.current_thread() is threading.main_thread():
            for signum in (signal.SIGTERM, signal.SIGINT):
                previous = signal.getsignal(signum)
                if isinstance(getattr(previous, '__self__', None), Server):
                    def handle(sig, frame, previous=previous):
                        self.request_stop()
                        previous(sig, frame)
                    self._signal_handlers[signum] = previous
                    signal.signal(signum, handle)
        self.initialized = True

    def request_stop(self) -> None:
        self.stopping.set()
        self.initialized = False
        if self.scheduler.running:
            self.scheduler.pause()

    async def tick(self) -> None:
        if self.stopping.is_set():
            return
        task = asyncio.current_task()
        self.job_tasks.add(task)
        try:
            if self.worker is None:
                self.worker = await self.container.get(VpnOutboxWorker)
            if not self.stopping.is_set():
                await self.worker.drain(self.stopping)
                await self.worker.reconcile(self.stopping)
        except Exception as exc:
            logger.error('VPN reconciliation cycle failed', error_type=type(exc).__name__)
        finally:
            self.job_tasks.discard(task)

    async def close(self) -> None:
        self.request_stop()
        try:
            tasks = self.http_tasks | self.job_tasks
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if self.scheduler.running:
                stopped = asyncio.Event()
                self.scheduler.add_listener(lambda event: stopped.set(), EVENT_SCHEDULER_SHUTDOWN)
                self.scheduler.shutdown(wait=True)
                await stopped.wait()
        finally:
            for signum, handler in self._signal_handlers.items():
                signal.signal(signum, handler)
            self._signal_handlers.clear()


class WorkTrackingMiddleware:
    def __init__(self, app: ASGIApp, runtime: VpnRuntime):
        self.app = app
        self.runtime = runtime

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return
        if self.runtime.stopping.is_set() and scope['path'] != '/healthz':
            await JSONResponse(status_code=503, content={
                'code': 'SERVICE_STOPPING', 'message': 'Service is shutting down',
            })(scope, receive, send)
            return
        task = asyncio.current_task()
        self.runtime.http_tasks.add(task)
        try:
            await self.app(scope, receive, send)
        finally:
            self.runtime.http_tasks.discard(task)
