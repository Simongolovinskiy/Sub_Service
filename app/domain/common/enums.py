from enum import StrEnum, auto


class UpperStrEnum(StrEnum):
    @staticmethod
    def _generate_next_value_(name, start, count, last_values) -> str:
        return name.upper()


class SecretsEnum(UpperStrEnum):
    DATABASE_CONNECTION_STRING = auto()

    LOG_LEVEL = auto()
