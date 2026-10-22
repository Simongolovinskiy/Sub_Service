from msgspec import Struct


class RegisterUserRequest(Struct, frozen=True, gc=False):
    email: str
    name: str
