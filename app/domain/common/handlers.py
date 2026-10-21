from typing import Protocol, TypeVar

from msgspec import Struct


Req = TypeVar("Req", contravariant=True)
Resp = TypeVar("Resp", covariant=True)


class Request(Struct):
    ...


class Response(Struct):
    ...


class RequestHandler(Protocol[Req, Resp]):
    async def handle(self, request: Req) -> Resp:
        ...
