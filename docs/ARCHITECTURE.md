# Architecture Documentation

## Overview

AsyncPyTemplate follows **Clean Architecture** principles with clear separation of concerns across layers.

## Layers

### 1. Domain Layer (`app/domain/`)

**Responsibility**: Core business logic and entities

- **DTO** - Domain entities (Product, etc.)
- **Errors** - Domain-specific exceptions
- **Ports** - Repository interfaces (contracts)
- **Core/Config** - Configuration models

**Key Principles**:
- No dependencies on other layers
- Pure business logic
- Framework-agnostic

### 2. Application Layer (`app/application/`)

**Responsibility**: Application entry points and orchestration

- **API** - HTTP/gRPC/WebSocket controllers
- **Middleware** - Request/response processing
- **Factory** - FastAPI app creation
- **Routers** - API routing

**Key Principles**:
- Delegates to use cases
- Handles HTTP concerns
- Thin controllers

### 3. Infrastructure Layer (`app/infrastructure/`)

**Responsibility**: External service integrations

- **Adapters**:
  - `persistence` - Database implementation (PostgreSQL)
  - `cache` - Redis caching
  - `amqp` - Kafka messaging
  - `monitoring` - Prometheus metrics
  - `di` - Dependency injection setup
  - `_logging` - Logging configuration

- **Ports** - Infrastructure interfaces

**Key Principles**:
- Implements domain ports
- Contains framework-specific code
- Swappable implementations

### 4. Services Layer (`app/services/`)

**Responsibility**: Application services and use cases

- **Use Cases** - Business logic workflows
  - Each use case: `request.py`, `response.py`, `handler.py`

**Key Principles**:
- Orchestrates domain logic
- Uses repositories
- Returns DTOs

## Data Flow

```
HTTP Request
    ↓
Middleware (work tracking, tracing, metrics, CORS)
    ↓
Controller (products.py)
    ↓
Use Case Handler (AddProductHandler)
    ↓
Unit of Work (Transaction)
    ↓
Repository (ProductRepository)
    ↓
Database (PostgreSQL)
```

## Dependency Injection

Uses **Dishka** for DI with scopes:

- `APP` - Application-wide singletons (connection pools)
- `REQUEST` - Per-request instances (connections, repositories)

**Container Structure**:
```python
ConfigProvider      # Loads config from env
PoolProvider        # DB connection pool
VpnProvider         # VPN settings, panel/cache clients, fresh UoW scopes
KafkaProvider       # Kafka broker
PersistenceProvider # Repositories, UoW
```

## Key Patterns

### Unit of Work

Manages transactions across repositories:

```python
async with uow as uow:
    await uow.products.add(product)
    # Auto-commit on success
    # Auto-rollback on exception
```

### Repository

Abstracts data access:

```python
class ProductRepositoryPort(Protocol):
    async def add(self, product: Product) -> None
    async def get_by_guid(self, guid: UUID) -> Product | None
```

### Optional Circuit Breaker Helper

The imported helper is not wired into the application or VPN path. Its optional usage is:

```python
@circuit_breaker(failure_threshold=5, recovery_timeout=60)
async def call_external_api():
    ...
```

## Performance Optimizations

1. **Connection Pooling**
   - PostgreSQL: 10-45 connections
   - VPN Redis client owns its connection pool; the imported generic `redis_pool.py` helper is not wired into the active VPN path.

2. **Async I/O**
   - All I/O operations are async
   - No blocking calls

3. **Caching**
   - Redis for distributed cache
   - VPN negative and idempotency caches use configured TTLs; see [VPN_API.md](VPN_API.md).

4. **Optional Helpers**
   - `RateLimitMiddleware` is imported template code and is not installed by `create_app()`.
   - Rate limiting is not active; no Redis counter or token-bucket protection is provided by the current application.

## Security

- Non-root Docker user
- Environment-based secrets
- Input validation (msgspec)
- CORS configuration

## Monitoring

- **Logs** - Structured JSON logs on stdout; collectors may forward them to Loki.
- **Metrics** - Prometheus metrics (request duration, count, etc.)
- **Traces** - OpenTelemetry distributed tracing
- **Health** - Root `/healthz` readiness and `/healthz?probe=liveness`; legacy `/api/v1/health` and `/api/v1/metrics` remain available.
