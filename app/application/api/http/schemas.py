from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, StringConstraints


class RegisterUserRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')

    email: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=254, pattern=r'^[^\s@]+@[^\s@]+\.[^\s@]+$')]
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class RegisteredUserResponse(BaseModel):
    id: UUID
    registered_at: AwareDatetime


class SubscriptionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    subscription_url: str
    expires_at: AwareDatetime
    status: Literal['pending', 'active', 'disabled']


class ErrorResponse(BaseModel):
    code: str
    message: str


class HealthResponse(BaseModel):
    status: Literal['alive', 'ready', 'not_ready']
