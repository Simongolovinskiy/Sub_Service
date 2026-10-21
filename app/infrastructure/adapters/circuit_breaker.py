from functools import wraps
from typing import Callable, Any
from circuitbreaker import circuit as cb_circuit

from app.infrastructure.adapters._logging import get_logger

logger = get_logger()


def circuit_breaker(
    failure_threshold: int = 5,
    recovery_timeout: int = 60,
    expected_exception: type[Exception] = Exception,
    name: str | None = None,
) -> Callable:
    """
    Circuit breaker decorator for external service calls.

    Args:
        failure_threshold: Number of failures before opening circuit
        recovery_timeout: Seconds before attempting to close circuit
        expected_exception: Exception type to catch
        name: Circuit breaker name for logging

    Example:
        @circuit_breaker(failure_threshold=3, recovery_timeout=30, name="payment_api")
        async def call_payment_api():
            ...
    """

    def decorator(func: Callable) -> Callable:
        circuit_name = name or func.__name__

        @cb_circuit(
            failure_threshold=failure_threshold,
            recovery_timeout=recovery_timeout,
            expected_exception=expected_exception,
            name=circuit_name,
        )
        @wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                result = await func(*args, **kwargs)
                logger.debug(f"Circuit breaker {circuit_name}: call succeeded")
                return result
            except expected_exception as e:
                logger.warning(
                    f"Circuit breaker {circuit_name}: failure",
                    error=str(e),
                    error_type=type(e).__name__,
                )
                raise

        return wrapper

    return decorator


