import os
from typing import Protocol, Type, TypeVar, Any

from dotenv import load_dotenv, find_dotenv

from app.domain.errors.adapters import SourceProviderError

T = TypeVar("T")

load_dotenv(dotenv_path=find_dotenv())


class SourceProviderPort(Protocol):
    def _load_source(self) -> None: ...

    def get_variable(
        self,
        name: str,
        type_: Type[T] = str,
        default: Any = ...,
    ) -> T | Any:
        ...


class EnvSourceProvider:
    def __init__(self) -> None:
        self._source: dict[str, str] = {}
        self._load_source()

    def _load_source(self) -> None:
        self._source = os.environ.copy()

    def get_variable(self, name: str, type_: Type[T] = str, default: Any = ...) -> T | Any:
        raw = self._source.get(name)

        if raw is None:
            if default is not ...:
                return default
            raise SourceProviderError(f"Required environment variable not found: {name}")

        try:
            return type_(raw)
        except TypeError as exc:
            raise SourceProviderError(
                f"Invalid value for {name}: {raw!r} cannot be converted to {type_.__name__}"
            ) from exc


class LockboxSourceProvider(SourceProviderPort):
    def _load_source(self) -> None:
        raise NotImplementedError("Lockbox not implemented yet")

    def get_variable(self, name: str, type_: Type[T] = str, default: Any = None) -> T:
        raise NotImplementedError


class VaultSourceProvider(SourceProviderPort):
    def _load_source(self) -> None:
        raise NotImplementedError("Vault not implemented yet")

    def get_variable(self, name: str, type_: Type[T] = str, default: Any = None) -> T:
        raise NotImplementedError
