# AsyncPyTemplate

Async Python service with FastAPI, asyncpg, yoyo migrations, APScheduler, Redis, a durable VPN outbox, and four Uvicorn workers. Requires Python 3.12+, uv, Docker, and Docker Compose v2.20+.

```sh
uv sync --locked
source .venv/bin/activate
pytest
```

Plain `pytest` and `make test` automatically provision a disposable PostgreSQL container through testcontainers and remove it after the session. Docker must be running. Supply `TEST_DATABASE_URL` to use an existing dedicated test database instead. Integration tests create/drop their own test schemas; do not point them at production.

```sh
make test
make run
make rebuild
```

`make run` starts PostgreSQL, builds the app, applies pending yoyo migrations once, then waits for the API to become ready at http://localhost:8000/healthz. Repeating it checks for new migrations. `make rebuild` refreshes base images and rebuilds without cached layers; normal `make run` preserves dependency-layer caching. PostgreSQL data survives both commands. `docker compose up --build -d --wait` also starts the stack with migrations before the API; use `make run` when applying newly added migrations to an existing stack.

Compose has local development defaults; copy `.env.example` to `.env` to customize ports and credentials before initial startup. Changing PostgreSQL credentials after its volume is initialized does not change existing database users. Host processes use the `DATABASE_CONNECTION_STRING` from `.env`; containers use the PostgreSQL service hostname automatically. Redis starts with the stack; Kafka and observability services remain optional.

CI runs tests with PostgreSQL and builds the runtime image in parallel, caching uv dependencies and Docker layers. The three-minute target requires measurement on GitHub runners; it is not a guaranteed duration.

Application, standard-library, Uvicorn supervisor/worker/access, and migration logs use Loguru's queued JSON sink on stdout, one JSON object per line. Kubernetes collectors can scrape stdout; the service does not write log files or send directly to Loki. `LOG_LEVEL` (default `INFO`) sets the shared threshold. Records include `extra.service`, `extra.request_id`, and valid OpenTelemetry trace/span IDs when available. Tracebacks omit local-variable diagnostics.

Queueing moves stdout writes to a background thread in each process; it is not coroutine stdout I/O. Application shutdown drains the queue even if container cleanup fails. Slow collectors can create backpressure; abrupt termination can lose queued logs. Uvicorn may emit its final worker shutdown message after the lifespan drain and re-raise SIGTERM before its exit hooks, so that final server message is not guaranteed. Logging queues belong to each process and are never passed to spawned workers. Where available, their synchronization primitives use the local `fork` context to avoid named semaphore leaks when Uvicorn bypasses exit hooks; Uvicorn still starts its workers with `spawn`. Run Uvicorn with `--log-config app/infrastructure/adapters/_logging/uvicorn.json` outside Docker too to route its own handlers through the same sink.

VPN API and error/idempotency contracts are documented in [docs/VPN_API.md](docs/VPN_API.md). Routes are `POST /users`, `POST /users/{user_id}/subscription`, `GET /users/{user_id}/subscription` and `POST /users/{user_id}/subscription/extend`. Use a distinct `Idempotency-Key` for every new mutation.

For local execution, copy `.env.example` to `.env`, start dependencies, apply migrations, then run the production launcher:

```sh
docker compose up -d --wait postgres redis
uv run python -c "import os; from dotenv import load_dotenv; from app.infrastructure.adapters.persistence.rdb.migration_apply import db_yoyo_migration; load_dotenv(); db_yoyo_migration(os.environ['DATABASE_CONNECTION_STRING'])"
uv run python -m app
```

`APP_WORKERS` defaults to four. On shutdown, new business HTTP requests return 503 `{"code":"SERVICE_STOPPING","message":"Service is shutting down"}` before dependency acquisition. Readiness `/healthz` returns 503; liveness `/healthz?probe=liveness` remains 200 without dependency work. The launcher grants already admitted HTTP requests 0.5 seconds and then waits for cancellation, transaction and scheduler cleanup before closing resources. Compose sets indefinite stop grace; SIGKILL and operator overrides can still interrupt it. The default five-minute scheduler performs durable retries, expiry disabling and latest-state reconciliation.

Configure the panel only through `VPN_PANEL_BASE_URL`, `VPN_PANEL_TOKEN` and the JSON UUID array `VPN_INTERNAL_SQUADS`. Placeholder defaults allow startup without contacting a live panel; empty squads do not prove usable VPN access. All timing and cache settings are in `.env.example`; full synchronous operation defaults to two seconds, negative cache to 30 seconds, and successful result cache to one day. Tests use disposable PostgreSQL/Redis and a fake local panel, including SIGTERM subprocess recovery:

```sh
uv run --locked pytest
docker compose up --build -d --wait
```
