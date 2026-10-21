from unittest.mock import MagicMock

import pytest

from tests.conftest import database_url


def test_database_container_is_removed_when_start_fails(monkeypatch):
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    container = MagicMock()
    container.start.side_effect = RuntimeError("readiness timed out")
    monkeypatch.setattr("testcontainers.community.postgres.PostgresContainer",
                        lambda *args, **kwargs: container)
    fixture = database_url.__wrapped__()
    with pytest.raises(pytest.fail.Exception, match="Start Docker or set TEST_DATABASE_URL"):
        next(fixture)
    container.stop.assert_called_once()
