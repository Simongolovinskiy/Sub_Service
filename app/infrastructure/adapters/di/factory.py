import logging

from app.domain.core.config.provider import SourceProviderPort, VaultSourceProvider
import os
from app.domain.core.config.provider import (
        EnvSourceProvider,
        LockboxSourceProvider,
    )
from app.domain.errors.adapters import SourceProviderError

logger = logging.getLogger(__name__)


async def provide_source_provider() -> SourceProviderPort:

    source_provider_name = os.getenv("SOURCE_PROVIDER", "env")

    if source_provider_name == "env":
        source_provider = EnvSourceProvider()
    elif source_provider_name == "lockbox":
        source_provider = LockboxSourceProvider()
    elif source_provider_name == "vault":
        source_provider = VaultSourceProvider()
    else:
        raise SourceProviderError(
            f"Wrong source provider: {source_provider_name}. "
            f"Expected 'env', 'vault', or 'lockbox'"
        )

    logger.info(f"Source provider initialized: {source_provider_name}")

    return source_provider
