import time
from typing import Callable

from fastapi import Request, Response
from prometheus_client import Counter, Histogram, Gauge, generate_latest, CONTENT_TYPE_LATEST
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response as StarletteResponse

# ============================================================================
# Metrics Definition
# ============================================================================

# HTTP Request metrics
http_requests_total = Counter(
    "http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status_code"],
)

http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration in seconds",
    ["method", "endpoint"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.075, 0.1, 0.25, 0.5, 0.75, 1.0, 2.5, 5.0, 7.5, 10.0),
)

http_requests_in_progress = Gauge(
    "http_requests_in_progress",
    "HTTP requests currently in progress",
    ["method", "endpoint"],
)

# Application metrics
app_info = Gauge(
    "app_info",
    "Application information",
    ["version", "service_name"],
)

# Database connection pool metrics
db_pool_size = Gauge(
    "db_pool_size",
    "Current database connection pool size",
)

db_pool_available = Gauge(
    "db_pool_available",
    "Available database connections in pool",
)

# Cache metrics
cache_hits_total = Counter(
    "cache_hits_total",
    "Total cache hits",
    ["cache_key_prefix"],
)

cache_misses_total = Counter(
    "cache_misses_total",
    "Total cache misses",
    ["cache_key_prefix"],
)


# ============================================================================
# Middleware
# ============================================================================

class PrometheusMiddleware(BaseHTTPMiddleware):

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Skip metrics endpoint itself
        if request.url.path == "/metrics":
            return await call_next(request)

        method = request.method
        endpoint = request.url.path

        # Track in-progress requests
        http_requests_in_progress.labels(method=method, endpoint=endpoint).inc()

        start_time = time.time()

        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception as e:
            status_code = 500
            raise
        finally:
            # Record metrics
            duration = time.time() - start_time

            http_requests_total.labels(
                method=method,
                endpoint=endpoint,
                status_code=status_code,
            ).inc()

            http_request_duration_seconds.labels(
                method=method,
                endpoint=endpoint,
            ).observe(duration)

            http_requests_in_progress.labels(
                method=method,
                endpoint=endpoint,
            ).dec()

        return response


# ============================================================================
# Metrics Endpoint Handler
# ============================================================================

def metrics_handler() -> StarletteResponse:
    return StarletteResponse(
        content=generate_latest(),
        media_type=CONTENT_TYPE_LATEST,
    )
