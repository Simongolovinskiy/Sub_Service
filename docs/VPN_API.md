# VPN users API

The four business routes are at the root. Existing `/api/v1` Product and health routes remain available. Interactive schemas are at `/api/docs` and `/api/openapi.json`.

| Method and route | Input | Confirmed response |
| --- | --- | --- |
| `POST /users` | `{"email":"user@example.com","name":"User"}` | 201 `{"id":"UUID","registered_at":"2026-10-21T12:00:00Z"}` |
| `POST /users/{user_id}/subscription` | Required `Idempotency-Key` header | 201 subscription |
| `GET /users/{user_id}/subscription` | UUID path | 200 local subscription |
| `POST /users/{user_id}/subscription/extend` | Required `Idempotency-Key` header | 200 subscription |

A subscription response contains exactly `subscription_url`, `expires_at` and `status` (`pending`, `active`, `disabled`). Dates use UTC and ISO 8601 `Z`. Creation grants seven days. Extension adds exactly seven days to the later of the current desired expiry and UTC now; callers cannot supply a duration. Status reflects the last confirmed processing state; expiry may elapse before the scheduled panel disable is confirmed. Internal panel credentials, protocol fields, numeric identity and desired state are never response fields.

Email and name must be meaningful nonblank strings. Email is trimmed and lowercased before uniqueness checking. Idempotency keys must be nonblank and at most 128 characters. A raw key is globally bound to one registered user and action: reusing it for another user/action returns 409. An unknown user returns 404 first. Retrying the same operation returns its original successful snapshot, including after later renewals and Redis eviction. Redis success snapshots default to 86400 seconds (`VPN_IDEMPOTENCY_TTL`); durable outbox results remain available after that cache TTL.

Errors use `{"code":"MACHINE_CODE","message":"Safe description"}`. Invalid JSON, fields, UUID or headers return 422 validation errors. Duplicate email/subscription and key conflicts return 409. Unknown users and missing local subscriptions return 404. Codes include `VALIDATION_ERROR`, `USER_ALREADY_EXISTS`, `SUBSCRIPTION_ALREADY_EXISTS`, `IDEMPOTENCY_CONFLICT`, `USER_NOT_FOUND`, `SUBSCRIPTION_NOT_FOUND`, `INTEGRATION_UNAVAILABLE`, `CACHE_UNAVAILABLE`, and `INTERNAL_ERROR`.

Panel failures return 500 `INTEGRATION_UNAVAILABLE` after a bounded synchronous operation (default two seconds, `VPN_OPERATION_TIMEOUT`). A committed pending intent remains durable and is retried. The response never invents a usable URL or stores the error as a successful idempotency result. GET reads local state only; a pending subscription without a usable key returns the same 500. A previously confirmed key can remain readable while renewal is pending.

A missing subscription is cached only after confirmed local absence, for 30 seconds by default (`VPN_NEGATIVE_CACHE_TTL`). A valid cached miss skips all database acquisition. Redis read/population/result-cache failures fall back to the database. Strict invalidation failure during mutation returns 500 `CACHE_UNAVAILABLE` and retains committed intent; it never acknowledges stale success.

Each process runs one APScheduler cycle every 300 seconds (`VPN_POLLING_SECONDS`), draining durable operations then reconciling latest desired state, including synced subscriptions. PostgreSQL locks serialize replicas. Retry starts at 0.2 seconds and caps at 300 seconds. Reconciliation eventually restores latest desired state after outstanding remote requests settle, subject to panel availability and cycle duration. Transient remote regression is possible; this is not remote version fencing. Successful historical snapshots are immutable.

`GET /healthz` checks initialized/not draining and pool resolution, acquire and `SELECT 1` within one total second: 200 `{"status":"ready"}` or 503 `{"status":"not_ready"}`. `GET /healthz?probe=liveness` returns 200 `{"status":"alive"}` without DB, Redis or panel work. Both send `Cache-Control: no-store`. Redis/panel outage alone does not fail DB readiness.

SIGTERM/SIGINT stops scheduling immediately. Newly arriving business HTTP requests return 503 `{"code":"SERVICE_STOPPING","message":"Service is shutting down"}` before request scope/dependency acquisition or service work. This applies to all four business routes, including GET. During shutdown, root readiness remains 503 `{"status":"not_ready"}` and liveness remains 200 `{"status":"alive"}` without dependency work. Uvicorn grants already admitted HTTP requests 0.5 seconds, then cancellation/transaction cleanup and active scheduler jobs finish before clients and pool close. Overall cleanup has no arbitrary cutoff. Docker uses an indefinite stop grace period. SIGKILL or an operator-imposed timeout can interrupt cleanup; durable retry handles unacknowledged operations after restart.

Set `VPN_PANEL_BASE_URL`, `VPN_PANEL_TOKEN` and `VPN_INTERNAL_SQUADS` (JSON array of squad UUIDs) for a real installation. Defaults target localhost with an empty token. Empty squads do not demonstrate usable VPN access. Live panel access was not tested. Payments, manual cancellation and subscription history are outside this MVP.
