from app.domain.errors.base import DomainError


class UserAlreadyExistsError(DomainError):
    def __init__(self):
        super().__init__(message="User already exists", code=409)
