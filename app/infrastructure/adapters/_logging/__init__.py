from __future__ import annotations

from loguru import logger
from loguru._logger import Logger

from app.domain.core.config.provider import EnvSourceProvider, SourceProviderPort

from .config import InterceptHandler, LoggerConfig

_INITIALIZED: bool = False


def get_logger(provider: SourceProviderPort | None = None) -> Logger:
    global _INITIALIZED

    if not _INITIALIZED:
        LoggerConfig.load(provider if provider is not None else EnvSourceProvider()).setup()
        _INITIALIZED = True

    return logger


def create_intercept_handler() -> InterceptHandler:
    get_logger()
    return InterceptHandler()
