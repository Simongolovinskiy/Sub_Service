import pytest
from argon2 import PasswordHasher

from app.infrastructure.adapters.security.password_hasher import Argon2PasswordHasher


@pytest.mark.asyncio
async def test_salted_argon2id_and_verification():
    hasher = Argon2PasswordHasher()
    first = await hasher.hash(" secret ")
    second = await hasher.hash(" secret ")
    assert first.startswith("$argon2id$")
    assert first != second
    assert PasswordHasher().verify(first, " secret ")
    assert await hasher.verify(" secret ", first)
    assert not await hasher.verify("wrong", first)
    assert not await hasher.verify("secret", "malformed")


@pytest.mark.asyncio
async def test_unexpected_hashing_failure_propagates(monkeypatch):
    hasher = Argon2PasswordHasher()

    def fail(*args):
        raise RuntimeError("unavailable")

    monkeypatch.setattr(hasher._hasher.__class__, "verify", fail)
    with pytest.raises(RuntimeError, match="unavailable"):
        await hasher.verify("secret", "encoded")


@pytest.mark.asyncio
async def test_argon2_verification_failure_propagates(monkeypatch):
    from argon2.exceptions import VerificationError

    hasher = Argon2PasswordHasher()

    def fail(*args):
        raise VerificationError("Memory allocation error")

    monkeypatch.setattr(hasher._hasher.__class__, "verify", fail)
    with pytest.raises(VerificationError, match="Memory allocation error"):
        await hasher.verify("secret", "encoded")
