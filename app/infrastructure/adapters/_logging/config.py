import logging
import multiprocessing
import sys
from typing import Self

import msgspec
from loguru import logger

from app.domain.common.enums import SecretsEnum
from app.domain.core.config.provider import SourceProviderPort

from .context import enrich
from .types import LogLevel


class LoggerConfig(msgspec.Struct):
    level: LogLevel = LogLevel.INFO

    @classmethod
    def load(cls, provider: SourceProviderPort) -> Self:
        return cls(
            level=provider.get_variable(SecretsEnum.LOG_LEVEL, LogLevel, default=LogLevel.INFO),
        )

    def setup(self) -> None:
        logger.remove()
        logger.configure(patcher=enrich)
        logger.add(
            sys.stdout,
            format='{message}',
            level=self.level,
            enqueue=True,
            context="fork" if "fork" in multiprocessing.get_all_start_methods() else None,
            serialize=True,
            colorize=False,
            diagnose=False,
            backtrace=False,
        )
        logging.basicConfig(handlers=[InterceptHandler()], level=logging.NOTSET, force=True)


_STANDARD_RECORD_KEYS = logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()


class InterceptHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        frame, depth = logging.currentframe(), 0
        while frame and (depth == 0 or frame.f_code.co_filename == logging.__file__):
            frame = frame.f_back
            depth += 1

        extra = {
            key: value for key, value in record.__dict__.items()
            if key not in _STANDARD_RECORD_KEYS
            and key not in ('message', 'asctime')
        }
        logger.bind(**extra).opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )
