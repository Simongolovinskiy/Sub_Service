from types import SimpleNamespace

import msgspec
import pytest

from app.infrastructure.adapters.security.password_hasher import Argon2PasswordHasher
from app.services.use_cases.users.add.handler import AddUserHandler
from app.services.use_cases.users.add.request import AddUserRequest


class MemoryUow:
    def __init__(self):
        self.saved = []
        self.users = SimpleNamespace(add=self.add)
        self.entered = False

    async def add(self, user):
        self.saved.append(user)

    async def __aenter__(self):
        self.entered = True
        return self

    async def __aexit__(self, *args):
        return None


@pytest.mark.asyncio
async def test_registration_hashes_password_and_uses_safe_defaults():
    uow = MemoryUow()
    hasher = Argon2PasswordHasher()
    result = await AddUserHandler(uow, hasher).handle(
        AddUserRequest(username=" alice ", password=" secret ", email=" Alice@Example.COM ")
    )
    user = uow.saved[0]
    assert user.username == "alice"
    assert user.email == "alice@example.com"
    assert user.is_active and not user.is_superadmin
    assert user.created_at.utcoffset() is not None
    assert await hasher.verify(" secret ", user.password)
    assert msgspec.to_builtins(result) == {"guid": str(user.guid)}
    assert result.guid == user.guid


@pytest.mark.asyncio
@pytest.mark.parametrize("values", [
    {"username": " "}, {"password": "\t"}, {"email": " "},
    {"username": None}, {"password": 42}, {"email": 42},
])
async def test_registration_rejects_blank_or_nonstring_input(values):
    uow = MemoryUow()
    fields = {"username": "alice", "password": "secret"} | values
    with pytest.raises(ValueError):
        await AddUserHandler(uow, Argon2PasswordHasher()).handle(AddUserRequest(**fields))
    assert not uow.entered
    assert not uow.saved


@pytest.mark.asyncio
async def test_hash_failure_happens_before_transaction():
    uow = MemoryUow()

    class FailingHasher:
        async def hash(self, password):
            assert not uow.entered
            raise RuntimeError("hashing failed")

    with pytest.raises(RuntimeError, match="hashing failed"):
        await AddUserHandler(uow, FailingHasher()).handle(AddUserRequest("alice", "secret"))
    assert not uow.entered
