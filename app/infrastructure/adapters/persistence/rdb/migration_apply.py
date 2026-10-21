import logging

from yoyo import get_backend, read_migrations

from app.domain.common.constants import MIGRATIONS_DIR
from app.infrastructure.adapters._logging import get_logger

logger = logging.getLogger(__name__)


def db_yoyo_migration(connection_string: str) -> None:
    get_logger()
    backend = get_backend(connection_string)
    migrations = read_migrations(MIGRATIONS_DIR)

    with backend.lock():
        applied_migrations = []
        try:
            migrations_to_apply = list(backend.to_apply(migrations))
            for migration in migrations_to_apply:
                backend.apply_one(migration)
                applied_migrations.append(migration)

        except Exception as e:
            for migration in reversed(applied_migrations):
                try:
                    backend.rollback_one(migration)
                except Exception as rollback_error:
                    logger.error(f"Rollback failed for migration {migration}: {rollback_error}")
            logger.error(f"Migration failed: {e}", exc_info=True)
            raise e
