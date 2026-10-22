from app.domain.common.handlers import RequestHandler
from app.domain.dto.vpn_user import RegisteredVpnUser
from app.domain.errors.vpn_user import VpnValidationError
from app.domain.ports.uow import UowFactory
from app.services.use_cases.users.register.request import RegisterUserRequest
from app.services.use_cases.users.register.response import RegisterUserResponse


class RegisterUserHandler(RequestHandler[RegisterUserRequest, RegisterUserResponse]):
    def __init__(self, uow_factory: UowFactory):
        self._uow_factory = uow_factory

    async def handle(self, request: RegisterUserRequest) -> RegisterUserResponse:
        email, name = request.email.strip().lower(), request.name.strip()
        if not name or len(name) > 200 or len(email) > 254 or email.count('@') != 1 or not all(email.split('@')):
            raise VpnValidationError()
        user = RegisteredVpnUser(email=email, name=name)
        async with self._uow_factory() as uow:
            async with uow:
                await uow.vpn_users.add(user)
        return RegisterUserResponse(id=user.guid, registered_at=user.registered_at)
