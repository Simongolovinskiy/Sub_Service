from enum import StrEnum, auto


class UpperStrEnum(StrEnum):
    @staticmethod
    def _generate_next_value_(name, start, count, last_values) -> str:
        return name.upper()


class CacheKey(StrEnum):
    PRODUCT = "product:"


# env names, example: os.getenv(SecretsEnum.DATABASE_CONNECTION_STRING)
class SecretsEnum(UpperStrEnum):
    # Common
    DATABASE_CONNECTION_STRING = auto()
    CACHE_HOST = auto()
    CACHE_PORT = auto()
    CACHE_DB = auto()
    CACHE_PASSWORD = auto()
    CACHE_SSL = auto()
    CACHE_DECODE_RESPONSES = auto()
    KAFKA_BOOTSTRAP_SERVERS = auto()
    KAFKA_GROUP_ID = auto()

    # Logging
    LOG_LEVEL = auto()
