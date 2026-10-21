"""Health check controllers."""
import asyncpg
from dishka import FromDishka
from dishka.integrations.fastapi import inject
from fastapi import APIRouter, Response, status

from app.infrastructure.adapters._logging import get_logger
from app.infrastructure.adapters.monitoring.prometheus import metrics_handler

logger = get_logger()
router = APIRouter()


@router.get("/health", status_code=status.HTTP_200_OK)
async def health_check() -> dict:
    """
    Basic health check endpoint.
    Returns 200 OK if service is running.
    """
    return {"status": "healthy", "service": "AsyncPyTemplate"}


@router.get("/ready", status_code=status.HTTP_200_OK)
@inject
async def readiness_check(
    pool: FromDishka[asyncpg.Pool],
    response: Response,
) -> dict:
    """
    Readiness probe endpoint.
    Checks if service can handle requests (DB connection, etc).
    """
    try:
        # Check database connection
        async with pool.acquire() as conn:
            await conn.fetchval("SELECT 1")

        return {
            "status": "ready",
            "checks": {
                "database": "ok",
            },
        }
    except Exception as e:
        logger.error("Readiness check failed", error=str(e))
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {
            "status": "not ready",
            "checks": {
                "database": "failed",
            },
            "error": str(e),
        }


@router.get("/metrics")
async def metrics():
    """
    Prometheus metrics endpoint.
    Returns metrics in Prometheus text format.
    """
    return metrics_handler()
