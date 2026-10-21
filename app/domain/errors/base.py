from __future__ import annotations

from typing import Any


class DomainError(Exception):
    def __init__(self, message: str, code: int = None, details: dict = None):
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details or {}

    def __str__(self):
        return self.message

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": self.__class__.__name__,
            "message": self.message,
            "code": self.code,
            **self.details,
        }
