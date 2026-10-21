from app.domain.common.handlers import RequestHandler
from app.domain.dto.user import User
from app.domain.ports.password_hasher import PasswordHasherPort
from app.infrastructure.ports.uow import UnitOfWorkPort

from .request import AddUserRequest
from .response import AddUserResponse


class AddUserHandler(RequestHandler[AddUserRequest, AddUserResponse]):
    def __init__(self, uow: UnitOfWorkPort, password_hasher: PasswordHasherPort):
        self._uow = uow
        self._password_hasher = password_hasher

    async def handle(self, request: AddUserRequest) -> AddUserResponse:
        for value in (request.username, request.password):
            if not isinstance(value, str) or not value.strip():
                raise ValueError("Username and password must be nonempty strings")
        if request.email is not None and (
            not isinstance(request.email, str) or not request.email.strip()
        ):
            raise ValueError("Email must be a nonempty string when provided")
        user = User(
            username=request.username.strip(),
            password=await self._password_hasher.hash(request.password),
            email=request.email.strip().lower() if request.email is not None else None,
        )
        async with self._uow as uow:
            await uow.users.add(user)
        return AddUserResponse(guid=user.guid)
