from app.domain.errors.base import DomainError


class VpnUserNotFoundError(DomainError):
    def __init__(self):
        super().__init__('VPN user not found', code=404)


class SubscriptionNotFoundError(DomainError):
    def __init__(self):
        super().__init__('Subscription not found', code=404)


class VpnUserAlreadyExistsError(DomainError):
    def __init__(self):
        super().__init__('Email already registered', code=409)


class SubscriptionAlreadyExistsError(DomainError):
    def __init__(self):
        super().__init__('Subscription already exists', code=409)


class IdempotencyConflictError(DomainError):
    def __init__(self):
        super().__init__('Idempotency key was used for a different operation', code=409)


class VpnValidationError(DomainError):
    def __init__(self):
        super().__init__('Invalid VPN request', code=422)


class PanelUnavailableError(DomainError):
    def __init__(self):
        super().__init__('External service unavailable; retry later', code=500,
                         details={'error_code': 'INTEGRATION_UNAVAILABLE'})


class CacheUnavailableError(DomainError):
    def __init__(self):
        super().__init__('Cache synchronization unavailable; retry later', code=500,
                         details={'error_code': 'CACHE_UNAVAILABLE'})
