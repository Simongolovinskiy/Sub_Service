from app.domain.core.config.provider import EnvSourceProvider, SourceProviderPort


async def provide_source_provider() -> SourceProviderPort:
    return EnvSourceProvider()
