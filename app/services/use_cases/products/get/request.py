import uuid
import msgspec


class GetProductRequest(msgspec.Struct, frozen=True, gc=False):
    guid: uuid.UUID