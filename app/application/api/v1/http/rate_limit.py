"""Rate limiting middleware using Redis."""
import time
from typing import Callable

from fastapi import Request, HTTPException, status
from starlette.middleware.base import BaseHTTPMiddleware

from app.infrastructure.adapters._logging import get_logger
from app.infrastructure.adapters.cache.redis_pool import RedisCache

logger = get_logger()


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Token bucket rate limiting middleware using Redis.

    Default: 100 requests per minute per IP.
    """

    def __init__(
        self,
        app,
        redis_cache: RedisCache,
        rate_limit: int = 100,  # requests
        time_window: int = 60,  # seconds
    ):
        super().__init__(app)
        self.redis_cache = redis_cache
        self.rate_limit = rate_limit
        self.time_window = time_window

    async def dispatch(self, request: Request, call_next: Callable):
        # Skip rate limiting for health checks
        if request.url.path in ["/health", "/ready", "/metrics"]:
            return await call_next(request)

        # Get client identifier (IP or custom header)
        client_id = self._get_client_id(request)

        # Check rate limit
        is_allowed = await self._check_rate_limit(client_id)

        if not is_allowed:
            logger.warning(
                "Rate limit exceeded",
                client_id=client_id,
                path=request.url.path,
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded. Try again later.",
                headers={"Retry-After": str(self.time_window)},
            )

        response = await call_next(request)

        # Add rate limit headers
        remaining = await self._get_remaining(client_id)
        response.headers["X-RateLimit-Limit"] = str(self.rate_limit)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(int(time.time()) + self.time_window)

        return response

    def _get_client_id(self, request: Request) -> str:
        """Extract client identifier from request."""
        # Try custom header first (for API keys)
        api_key = request.headers.get("X-API-Key")
        if api_key:
            return f"api_key:{api_key}"

        # Fall back to IP address
        client_ip = request.client.host if request.client else "unknown"
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            client_ip = forwarded_for.split(",")[0].strip()

        return f"ip:{client_ip}"

    async def _check_rate_limit(self, client_id: str) -> bool:
        """Check if client is within rate limit using sliding window."""
        key = f"rate_limit:{client_id}"

        try:
            # Increment counter
            current = await self.redis_cache.incr(key)

            # Set expiration on first request
            if current == 1:
                await self.redis_cache.expire(key, self.time_window)

            return current <= self.rate_limit
        except Exception as e:
            logger.error("Rate limit check failed", client_id=client_id, error=str(e))
            # Fail open - allow request if Redis is down
            return True

    async def _get_remaining(self, client_id: str) -> int:
        """Get remaining requests for client."""
        key = f"rate_limit:{client_id}"
        try:
            current = int(await self.redis_cache.client.get(key) or 0)
            return max(0, self.rate_limit - current)
        except:
            return self.rate_limit
