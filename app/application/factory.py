from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from dishka import AsyncContainer
from dishka.integrations.fastapi import setup_dishka
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.application.api.http.health import router as health_router
from app.application.api.http.middleware import TracingMiddleware
from app.application.api.http.vpn_users import router as vpn_router
from app.application.runtime import VpnRuntime, WorkTrackingMiddleware
from app.domain.common.constants import SERVICE_NAME
from app.domain.errors import vpn_user as vpn_errors
from app.domain.errors.base import DomainError
from app.infrastructure.adapters._logging import get_logger
from app.infrastructure.adapters.di.main import build_container
from app.infrastructure.adapters.monitoring.prometheus import (
    PrometheusMiddleware,
    app_info,
)

logger = get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    logger.info("Starting application", service=SERVICE_NAME)

    container: AsyncContainer = app.state.container
    runtime = app.state.runtime
    try:
        await runtime.start(container)
        yield
    finally:
        logger.info("Shutting down application")
        try:
            try:
                await runtime.close()
            finally:
                await container.close()
            logger.info("Application stopped gracefully")
        finally:
            await logger.complete()


def create_app() -> FastAPI:

    app = FastAPI(
        title=SERVICE_NAME,
        description="VPN subscription service",
        version="0.1.0",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )

    # Build DI container
    container = build_container()
    app.state.container = container
    app.state.runtime = VpnRuntime()

    # Setup Dishka integration with FastAPI
    setup_dishka(container, app)

    # Configure CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # Configure in production
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Add custom middleware (order matters - last added = first executed)
    app.add_middleware(PrometheusMiddleware)
    app.add_middleware(TracingMiddleware)
    app.add_middleware(WorkTrackingMiddleware, runtime=app.state.runtime)

    # Set application info metric
    app_info.labels(version="0.1.0", service_name=SERVICE_NAME).set(1)

    # Setup exception handlers
    setup_exception_handlers(app)

    # Include routers
    app.include_router(health_router)
    app.include_router(vpn_router)

    logger.info("FastAPI application configured")

    return app


def setup_exception_handlers(app: FastAPI) -> None:
    vpn_codes = {
        vpn_errors.VpnUserNotFoundError: 'USER_NOT_FOUND',
        vpn_errors.SubscriptionNotFoundError: 'SUBSCRIPTION_NOT_FOUND',
        vpn_errors.VpnUserAlreadyExistsError: 'USER_ALREADY_EXISTS',
        vpn_errors.SubscriptionAlreadyExistsError: 'SUBSCRIPTION_ALREADY_EXISTS',
        vpn_errors.IdempotencyConflictError: 'IDEMPOTENCY_CONFLICT',
        vpn_errors.VpnValidationError: 'VALIDATION_ERROR',
        vpn_errors.PanelUnavailableError: 'INTEGRATION_UNAVAILABLE',
        vpn_errors.CacheUnavailableError: 'CACHE_UNAVAILABLE',
    }

    async def vpn_error_handler(request: Request, exc: DomainError) -> JSONResponse:
        code = vpn_codes[type(exc)]
        message = 'Request validation failed' if code == 'VALIDATION_ERROR' else exc.message
        return JSONResponse(status_code=exc.code, content={'code': code, 'message': message})

    for error in vpn_codes:
        app.add_exception_handler(error, vpn_error_handler)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={'code': 'VALIDATION_ERROR', 'message': 'Request validation failed'},
        )

    @app.exception_handler(Exception)
    async def generic_error_handler(request: Request, exc: Exception) -> JSONResponse:
        if request.url.path == '/users' or request.url.path.startswith('/users/'):
            logger.error('VPN request failed', error_type=type(exc).__name__)
        else:
            logger.opt(exception=exc).error('Unexpected error occurred', error_type=type(exc).__name__)
        return JSONResponse(
            status_code=500,
            content={'code': 'INTERNAL_ERROR', 'message': 'An unexpected error occurred'},
        )
