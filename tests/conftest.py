import os

import pytest


@pytest.fixture(scope="session")
def database_url():
    configured = os.environ.get("TEST_DATABASE_URL")
    if configured:
        yield configured
        return

    from testcontainers.community.postgres import PostgresContainer

    postgres = None
    try:
        try:
            postgres = PostgresContainer("postgres:16-alpine", driver=None)
            postgres.start()
        except Exception as exc:
            pytest.fail(
                "Cannot start disposable PostgreSQL. Start Docker or set TEST_DATABASE_URL "
                f"to a disposable test database: {exc}",
                pytrace=False,
            )
        yield postgres.get_connection_url()
    finally:
        if postgres is not None:
            postgres.stop()
