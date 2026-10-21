from typing import Self

import msgspec


from app.domain.common.enums import SecretsEnum
from app.domain.core.config.provider import SourceProviderPort


class DatabaseConfig(msgspec.Struct):
    connection_string: str

    @classmethod
    def load(cls, source_provider: SourceProviderPort) -> Self:
        return cls(connection_string=source_provider.get_variable(SecretsEnum.DATABASE_CONNECTION_STRING, str))


class CacheConfig(msgspec.Struct):
    host: str
    port: int
    db: int
    password: str | None = msgspec.field(default=None)
    ssl: bool | None = msgspec.field(default=None)
    decode_responses: bool = msgspec.field(default=True)  # return str instead bytes

    @classmethod
    def load(cls, source_provider: SourceProviderPort) -> Self:
        return cls(
            host=source_provider.get_variable(SecretsEnum.CACHE_HOST, str),
            port=source_provider.get_variable(SecretsEnum.CACHE_PORT, int),
            db=source_provider.get_variable(SecretsEnum.CACHE_DB, int),
            password=source_provider.get_variable(SecretsEnum.CACHE_PASSWORD, str, default=None),
            ssl=source_provider.get_variable(SecretsEnum.CACHE_SSL, bool, default=None),
            decode_responses=source_provider.get_variable(SecretsEnum.CACHE_DECODE_RESPONSES, bool, default=True),
        )


class KafkaConfig(msgspec.Struct):
    bootstrap_servers: str
    consumer_group_id: str

    @classmethod
    def load(cls, source_provider: SourceProviderPort) -> Self:
        return cls(
            bootstrap_servers=source_provider.get_variable(
                SecretsEnum.KAFKA_BOOTSTRAP_SERVERS, str
            ),
            consumer_group_id=source_provider.get_variable(
                SecretsEnum.KAFKA_GROUP_ID, str
            ),
        )
