# VPN Users MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development task-by-task. Checkbox steps track progress.

**Goal:** Implement the four subscription endpoints from the supplied Python backend assignment with durable recovery, Redis caching, graceful shutdown and health checks.

**Architecture:** Preserve existing auth users and Product examples. Add registered VPN customers, their one-to-one subscription, and an outbox. Commit intent before a synchronous panel attempt; PostgreSQL locks serialize operations across workers, and panel results plus event acknowledgements commit together. APScheduler in lifespan retries pending work and handles expiry every five minutes.

**Tech Stack:** Existing Python 3.12/FastAPI/asyncpg/Dishka/msgspec/yoyo/Redis/pytest; HTTPX promoted to runtime; required pydantic-settings; APScheduler 3.x AsyncIOScheduler.

## Source of Truth

- `/Users/simon/Downloads/2_5386489034770003504.pdf`: full four-page original assignment, now read completely.
- `/Users/simon/Downloads/Вопросы по ТЗ.md`: user has inserted answers; these override earlier provisional defaults and asynchronous 202 proposals.
- `/Users/simon/Downloads/remnawave-api-reference-2026-10-21.md` and `remnawave-openapi-3.4.5.json`: exact v3 panel contract.

## Global Constraints

- Canonical delivery repository: `/Users/simon/PycharmProjects/personal/Sub_Service`, branch `main`, target initial commit cca309d. The implementation originated in AsyncPyTemplate on feat/vpn-users against aff9b44 after the external logging task; its original workspace is preserved.
- Preserve existing auth registration, Product behavior and all foreign changes. The other agent's logging/factory/Docker/env/dependency work is committed in aff9b44 and may now be integrated. Never broad-stage, clean, reset, rebase or force-push. The user explicitly authorized the final commit and push to Sub_Service/main.
- Follow the existing layer boundaries and UnitOfWork transaction implementation. Graph discovery first, then Serena. No new code comments or unnecessary abstractions.
- No real panel mutations, embedded credentials or secret logging. Only mocked/local panel tests. Write a clear report of unrun live checks.
- All new timestamps are timezone-aware UTC; API dates use ISO 8601 Z. Period is exactly seven days. The user timezone is deferred presentation logic.
- Public errors share a machine-readable code and message. Unknown user or absent subscription returns404; duplicate normalized email returns409.
- A panel timeout can mean success: durable intent, stable remote ownership and reconciliation are mandatory. Do not adopt an unrelated account by matching username alone.
- A timed-out remote PATCH can finish after a newer acknowledged operation. Each five-minute cycle must also reconcile the latest durable desired state of synced subscriptions under per-user locks. Preserve paid targets and historical idempotency responses; after expiry enforce DISABLED. Guarantee eventual convergence after remote work settles and the panel is available, not strict remote fencing absent a panel version contract.
- Redis caches idempotent successful responses with configurable TTL (working default24h; final TTL still open). Durable DB event identity/result survives Redis eviction/restart. Failed/ambiguous500 responses are not cached as completed.
- Negative cache must give immediate visibility after successful subscription creation, including concurrent readers. Epoch/fencing prevents stale missing-result publication. No DB query on a valid cached404. DB fallback on cache-read error remains. If required invalidation fails, creation reports500 with durable pendingintent and retries invalidation before successful publication; stale404 after a reported successful creation is not acceptable.
- Four Uvicorn processes/replicas require PostgreSQL pessimistic serialization, never process-local locks as the only protection. Process one event under event+user/subscription row locks through a bounded external operation; cancellation rolls back/releases locks.
- Retain processed events. Use a distinct superseded outcome for skipped old expiry events. Payments, manual cancellation and full audit endpoints remain outside MVP.
- APScheduler starts in lifespan and scans every300seconds. Stop future job launches on shutdown, finish/cancel safely, then close resources. HTTP graceful wait is0.5seconds. Overall cleanup has no arbitrary cutoff that would violate local consistency; target roughly5seconds and bound external calls to2seconds.
- Health DB timeout1second. Liveness does no dependency calls. Temporary panel outage does not fail readiness because durable intent can be retained. Redis invalidation errors may fail affected mutations while reads can use DB fallback.

## Exact MVP HTTP Contract

Four business endpoints at ROOT, plus technical health route:

1. `POST /users`: `{email,name}`. Generate local UUID and UTC registration timestamp. Local registration only,201 withid; unique normalized email409.
2. `POST /users/{user_id}/subscription`: Idempotency-Key header, create one panel user for7days from actual panel creation attempt; return201 withsubscription_url andexpires_at only after confirmation. Existing subscription cannot produce another panel user. Pending durable intent is retained if response500.
3. `GET /users/{user_id}/subscription`: read LOCAL DB, return200 withurl,expiry,status;404 unknown user/no subscription. A pending intent without a usable key is represented by a documented machine-readable pending error, never a cached absence or fabricated URL.
4. `POST /users/{user_id}/subscription/extend`: Idempotency-Key header, exactly7days; persist target `max(now_utc,current desired/confirmed expiry)+7days` once per operation. PATCH absolute expireAt and ACTIVE to re-enable expired/disabled accounts. Return200 withconfirmed result. No public days or expires_at input.
5. `GET /healthz`: readiness200 `{"status":"ready"}` or503 `{"status":"not_ready"}`; `?probe=liveness` returns200 `{"status":"alive"}` when the process answers. Retain legacy health routes. No cached probe or secret diagnostics.

Idempotency-Key is nonblank text up to128characters, bound to a durable user+operation fingerprint; reuse of the same raw key for a different user/action returns409. The service synchronously attempts panel work within2seconds, with first retry delay0.2seconds and exponential increases when budget permits. On external unavailability return500 with a clear code/message and pending outbox. Poll recovery remains every300seconds, retry-delay cap300seconds. Missing-cache default TTL30seconds; successful-idempotency response cache default TTL86400seconds, both configurable.

Creation intent has nullable expiry while the panel account is unknown. Check deterministic username plus stable ownership marker before retrying creation. On confirmed absence calculate attemptnow+7days and POST. On a previously successful owned creation use panel's existing expiry, preserving the original paid period. Persist final actual expiry and schedule expiry event with result acknowledgement.

## Task 1: Durable VPN Core

**Ownership:** new VPN DTO/errors/ports/services/repos/client/cache adapters,000003 migrations, existing UOW port/adapter, minimal PersistenceProvider wiring, core tests and affected existing constructor/migration tests. Do not change API/factory, env, dependency, Docker or foreign untracked files.

**Files:** new `app/domain/dto/vpn_user.py`, errors/ports in existing directories; repositories under `app/infrastructure/adapters/persistence/rdb/repositories/`; new VPN service/processor in services; new HTTPX panel and Redis adapters; `migrations/000003_vpn_users_outbox.sql` and rollback depending000002_users; existing `app/infrastructure/ports/uow.py`, RDBUOW, and persistence wiring only in DI main.py; focused tests.

**Binding entities:** `vpn_users(guid,email,name,registered_at)`, `vpn_subscriptions(user_guid PK/FK,panelid,url,expiry,status,version,traffic,created/updated and pending desired-state fields as needed)`, `outbox_events` referencing the VPN user and carrying stable idempotency identity, immutable input/target, result snapshot, retry/publication/supersession metadata. Keep auth `users` unchanged.

**Service interfaces:** registered-user and subscription operations are distinct. Provide `register(email,name)`, `create_subscription(user_guid,idempotency_key:str)`, `get_subscription(user_guid)`, `extend_subscription(user_guid,idempotency_key:str)`. Define/report exact DTOs and constructors. Share one processor between synchronous calls and scheduled recovery; fresh UoW/connection scopes and pessimistic locks prevent duplicate work. Services must not import Dishka, FastAPI or drivers.

- [x] Write failing real-PostgreSQL tests with random schemas/all migrations; prove registration uniqueness, atomic subscription/outbox intent and result/acknowledgement.
- [x] Implement additive tables/repositories and extend the existing UoW with required repositories; update all production constructor callers and migration rollback expectations.
- [x] Persist idempotency identity before external calls; identical retry returns the original successful result, different request/key reuse conflicts; renewal target is calculated once.
- [x] Implement seven-day panel creation with stable ownership reconciliation and absolute renewal+ACTIVE. Actual panel POST success201, PATCH200. Sanitize failures and enforce the two-second full-operation budget.
- [x] Implement the shared synchronous/scheduled processor. Temporary failures commit retry metadata without acknowledgement and surface a service error; cancellation rolls back. Serial processing and stale-expiry checks protect renewal.
- [x] Add recurring latest desired-state repair for synced subscriptions, proving late old renewal/disable completion converges without adding paid time or changing historical results. Unknown-user404 precedes raw-key409 conflict.
- [x] Implement Redis idempotency success cache plus strict negative-cache fencing/invalidation. Use the stable Redis library, preserve the user's untracked redis_pool.py. Do not introduce per-request DB checks on cached404.
- [x] Verify timeout-after-panel-success, local-final-commit failure, restart recovery, same-key retries, two concurrent creates, concurrent renewals without lost days, old expiry after renewal, cache race, Redis read fallback and invalidation failure policy.
- [x] Run focused core and affected baseline tests; report exact interfaces and changed paths. Parent reviews before explicit-path commits.

## Task 2: Four Routes and Runtime

**Ownership:** root API schemas/controllers, new VPN DI provider/config, routers/factory/DI composition after shared-file coordination, pydantic-settings/HTTPX/APScheduler runtime dependencies, API/process tests.

- [x] Write failing tests for exact root routes/headers/status/error format, email/name validation, UTC Z output and filtering protocol secrets.
- [x] Wire the completed core into Dishka scopes. Configuration uses pydantic-settings; credentials come only from environment. Do not hardcode domain/config values in routers.
- [x] Add root healthz and accepted probe selector,1second bounded DB readiness, liveness without dependency calls. Panel outages must not cause liveness failure.
- [x] Start APScheduler jobs in lifespan (coalesced, max_instances1 per process); PostgreSQL locks provide cross-process correctness. Uvicorn runs HTTP, APScheduler schedules recovery/expiry jobs.
- [x] Implement graceful SIGTERM behavior with0.5second HTTP wait, cooperative scheduler stop and safe active-operation finalization. Do not acknowledge events in unconditional cleanup. Close clients and pool after task completion.
- [x] Add real-process SIGTERM and end-to-end tests using isolated DB and a local fake panel, not the real host.

## Task 3: Delivery

- [x] Integrate Compose Redis and pydantic-settings/env examples, required runtime dependencies and safe shutdown launch settings, preserving concurrent logging edits.
- [x] Document exact four contracts, errors, idempotency/cache behavior, UTC, seven-day policy, retry/recovery and startup commands. No secrets.
- [x] Run full pytest, scoped lint/static checks and Docker Compose/build validation; baseline before VPN was41passed.
- [x] Independent review of final implementation against PDF and answered questions, then fix findings and rerun covering tests.
- [x] Commit only verified owned paths/hunks with imperative subjects and no co-author trailers. Do not push.

## Progress

- [x] Explore repo and verify initial41tests passing.
- [x] Read full PDF and user's answered questions; supersede old provisional async202 plan.
- [x] Task1 complete: independent spec/quality PASS after two fix waves; parent113tests passed8.52sec. Reports in .superpowers/sdd/.
- [x] Task2 complete: four routes, healthz, lazy DI, scheduler and graceful shutdown; final independent review accepted.
- [x] Task3 verified: 149 tests passed in the canonical checkout, scoped Ruff and isolated Compose smoke passed; all review findings closed. Explicit project-file commit and push authorized by the user.
