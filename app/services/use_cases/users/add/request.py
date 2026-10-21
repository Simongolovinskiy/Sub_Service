from msgspec import Struct


class AddUserRequest(Struct, frozen=True, gc=False):
    username: str
    password: str
    email: str | None = None
