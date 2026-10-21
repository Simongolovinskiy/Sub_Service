# Project Structure

## Layer Organization

### Application Layer (`app/application/`)

```
application/
├── api/v1/http/
│   ├── controllers/         # HTTP endpoint handlers
│   │   ├── products.py     # Product endpoints
│   │   └── health.py       # Health/metrics endpoints
│   ├── schemas/            # API layer DTOs (HTTP concerns)
│   │   └── products.py     # API request/response schemas
│   ├── middleware.py       # Tracing, Logging middleware
│   ├── rate_limit.py       # Optional rate-limit helper (not installed)
│   └── routers.py          # API routing
└── factory.py              # FastAPI app factory
```

**Responsibilities**:
- HTTP protocol concerns
- API schemas (request/response validation)
- Routing and middleware
- Mapping between API DTOs ↔ Use Case DTOs

**Key principle**: API schemas are separate from use case DTOs

### Services Layer (`app/services/`)

```
services/
└── use_cases/
    └── products/
        ├── add/
        │   ├── handler.py      # AddProductHandler
        │   ├── request.py      # AddProductRequest (use case input)
        │   └── response.py     # AddProductResponse (use case output)
        └── get/
            ├── handler.py      # GetProductHandler
            ├── request.py      # GetProductRequest
            └── response.py     # GetProductResponse
```

**Responsibilities**:
- Business logic orchestration
- Use case execution
- Transaction management (via UoW)

**Key principle**: Each use case has its own request/response DTOs

### Domain Layer (`app/domain/`)

```
domain/
├── dto/                    # Domain entities
│   ├── product.py         # Product entity
│   └── broker.py          # Message broker DTOs
├── errors/                # Domain exceptions
│   ├── base.py           # DomainError
│   └── product.py        # ProductNotFoundError
├── ports/                 # Repository interfaces
│   └── repositories/
│       └── product.py    # ProductRepositoryPort
└── core/config/          # Configuration
    ├── settings.py       # Config models
    └── provider.py       # Config provider
```

**Responsibilities**:
- Core business entities
- Business rules
- Domain exceptions
- Repository interfaces (ports)

**Key principle**: No dependencies on outer layers

### Infrastructure Layer (`app/infrastructure/`)

```
infrastructure/
├── adapters/
│   ├── persistence/rdb/
│   │   ├── repositories/
│   │   │   └── product.py    # RDBProductRepository (implements port)
│   │   └── uow.py            # RDBUnitOfWork
│   ├── cache/
│   │   ├── redis_pool.py     # Redis connection pool
│   │   └── product_cache.py  # Product cache implementation
│   ├── monitoring/
│   │   └── prometheus.py     # Prometheus metrics
│   ├── di/
│   │   └── main.py          # DI container setup
│   └── _logging/
│       └── config.py        # Logging configuration
└── ports/                   # Infrastructure interfaces
    ├── uow.py              # UnitOfWorkPort
    └── product_cache.py    # ProductCachePort
```

**Responsibilities**:
- External service integrations
- Framework-specific code
- Implementation of domain/infrastructure ports

## DTO Flow

```
HTTP Request (JSON)
    ↓
API Schema (CreateProductRequest)           # app/application/api/v1/http/schemas/
    ↓ mapping
Use Case Request (AddProductRequest)        # app/services/use_cases/products/add/request.py
    ↓ handler execution
Use Case Response (AddProductResponse)      # app/services/use_cases/products/add/response.py
    ↓ mapping
API Schema (ProductResponse)                # app/application/api/v1/http/schemas/
    ↓
HTTP Response (JSON)
```

### Why Separate API DTOs from Use Case DTOs?

1. **Separation of Concerns**
   - API schemas handle HTTP validation (headers, query params, etc.)
   - Use case DTOs are pure business logic inputs/outputs

2. **Flexibility**
   - API can change without affecting business logic
   - Multiple APIs (REST, GraphQL, gRPC) can use same use cases

3. **Versioning**
   - API v1 and v2 can map to same use cases
   - Business logic remains stable

4. **Clean Architecture**
   - Application layer depends on Services layer
   - Services layer is independent of delivery mechanism

## Running the Application

Run these commands from the repository root after installing dependencies and configuring `.env`:

```bash
docker compose up -d --wait postgres
docker compose up -d redis
```

Apply migrations as described in [README.md](../README.md), then start the production launcher:

```bash
source .venv/bin/activate
python -m app
```

Docker uses the same launcher with `CMD ["python", "-m", "app"]`. It configures four Uvicorn workers by default, JSON logging, and the 0.5-second HTTP shutdown budget; runtime cleanup finishes before dependencies close. `entrypoint.py` remains the app factory import target, not the server command.
