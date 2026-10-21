from typing import Annotated
from uuid import UUID

from dishka.integrations.fastapi import FromDishka, inject
from fastapi import APIRouter, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from app.application.api.http.schemas import (
    ErrorResponse,
    RegisteredUserResponse,
    RegisterUserRequest,
    SubscriptionResponse,
)
from app.domain.errors.base import DomainError
from app.domain.errors.vpn_user import VpnValidationError
from app.infrastructure.adapters._logging import get_logger
from app.services.vpn_users import VpnUserService


class VpnRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def safe_handler(request: Request):
            try:
                return await handler(request)
            except (DomainError, RequestValidationError):
                raise
            except Exception as exc:
                get_logger().error('VPN request failed', error_type=type(exc).__name__)
                return JSONResponse(status_code=500, content={'code': 'INTERNAL_ERROR', 'message': 'An unexpected error occurred'})
        return safe_handler


router = APIRouter(route_class=VpnRoute, tags=['VPN users'], responses={
    **{code: {'model': ErrorResponse} for code in (404, 409, 422, 500)},
    503: {'model': ErrorResponse, 'description': 'Service is shutting down', 'content': {
        'application/json': {'example': {'code': 'SERVICE_STOPPING', 'message': 'Service is shutting down'}},
    }},
})
IdempotencyKey = Annotated[str, Header(alias='Idempotency-Key', min_length=1, max_length=128)]


@router.post('/users', status_code=201, response_model=RegisteredUserResponse)
@inject
async def register_user(payload: RegisterUserRequest, service: FromDishka[VpnUserService]) -> RegisteredUserResponse:
    user = await service.register(payload.email, payload.name)
    return RegisteredUserResponse(id=user.guid, registered_at=user.registered_at)


@router.post('/users/{user_id}/subscription', status_code=201, response_model=SubscriptionResponse)
@inject
async def create_subscription(user_id: UUID, service: FromDishka[VpnUserService], idempotency_key: IdempotencyKey) -> SubscriptionResponse:
    if not idempotency_key.strip():
        raise VpnValidationError()
    return SubscriptionResponse.model_validate(await service.create_subscription(user_id, idempotency_key))


@router.get('/users/{user_id}/subscription', response_model=SubscriptionResponse)
@inject
async def get_subscription(user_id: UUID, service: FromDishka[VpnUserService]) -> SubscriptionResponse:
    return SubscriptionResponse.model_validate(await service.get_subscription(user_id))


@router.post('/users/{user_id}/subscription/extend', response_model=SubscriptionResponse)
@inject
async def extend_subscription(user_id: UUID, service: FromDishka[VpnUserService], idempotency_key: IdempotencyKey) -> SubscriptionResponse:
    if not idempotency_key.strip():
        raise VpnValidationError()
    return SubscriptionResponse.model_validate(await service.extend_subscription(user_id, idempotency_key))
