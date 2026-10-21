import asyncio
import logging

import requests

from app.domain.core.config.provider import EnvSourceProvider
from app.infrastructure.adapters._logging import get_logger
from app.infrastructure.adapters.di.main import build_container
from app.infrastructure.ports.uow import UnitOfWorkPort


async def main() -> None:
    logger_instance = get_logger()
    for i in range(100):
        logger_instance.error("Starting application...")
        logger_instance.error("Error message 1")
    await asyncio.sleep(2)

# async def test_uow(container):
#     async with container() as request_container:
#         uow = await request_container.get(UnitOfWorkPort)
#         print(f"UoW: {uow._conn}")
#         await asyncio.sleep(0.1)

if __name__ == "__main__":
    asyncio.run(main())