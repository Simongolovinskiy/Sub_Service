from uuid import UUID

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.domain.dto.vpn_user import VpnSettings


class VpnRuntimeSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix='VPN_', env_file='.env', extra='ignore', hide_input_in_errors=True)

    panel_base_url: str = 'http://127.0.0.1:8080'
    panel_token: SecretStr = SecretStr('')
    redis_url: SecretStr = SecretStr('redis://localhost:6379/0')
    internal_squads: tuple[UUID, ...] = ()
    operation_timeout: float = Field(default=2, gt=0)
    retry_initial: float = Field(default=0.2, gt=0)
    retry_max: float = Field(default=300, gt=0)
    polling_seconds: float = Field(default=300, gt=0)
    cache_timeout: float = Field(default=0.2, gt=0)
    negative_cache_ttl: int = Field(default=30, gt=0)
    idempotency_ttl: int = Field(default=86400, gt=0)

    def domain_settings(self) -> VpnSettings:
        return VpnSettings(**self.model_dump(exclude={'panel_base_url', 'panel_token', 'redis_url'}))


class LaunchSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix='APP_', env_file='.env', extra='ignore', hide_input_in_errors=True)

    workers: int = Field(default=4, gt=0)
    port: int = Field(default=8000, gt=0, le=65535)
