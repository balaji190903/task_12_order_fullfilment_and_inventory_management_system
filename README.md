# Order Fulfillment & Inventory Management Platform

A production-oriented REST API for order fulfillment: customers place orders, warehouse
managers manage inventory, fulfillment agents process orders, and admins manage products,
warehouses, and users. Built with **FastAPI + PostgreSQL + SQLAlchemy + Alembic**.

## Table of contents

- [Quick start](#quick-start)
- [Architecture](#architecture)
- [Database design](#database-design)
- [Concurrency strategy](#concurrency-strategy) — required reading for reviewers
- [Authentication & authorization](#authentication--authorization)
- [Order state machine](#order-state-machine)
- [Idempotency](#idempotency)
- [Error format](#error-format)
- [API overview](#api-overview)
- [Testing](#testing)
- [Project layout](#project-layout)
- [What's not implemented / trade-offs](#whats-not-implemented--trade-offs)

## Quick start

### Option A — Docker Compose (recommended)

```bash
cp .env.example .env
docker compose up --build
```

This starts Postgres and the API (migrations run automatically on container start).
API is at `http://localhost:8000`, interactive docs at `http://localhost:8000/docs`.

### Option B — Local Python + local/remote Postgres

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# edit .env: set DATABASE_URL to point at your Postgres instance

alembic upgrade head
uvicorn app.main:app --reload
```

### Running tests

Tests run against a **real PostgreSQL database** (not SQLite/mocks) so that the
concurrency and locking behavior under test is the same as production. Create a
second database for tests (or reuse one, it truncates between tests and drops/recreates
the schema once per session):

```bash
# via psql:
createdb order_fulfillment_test

# then:
DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/order_fulfillment_test \
  pytest -v
```

(`tests/conftest.py` defaults to this URL if the environment variable is unset.)

All 47 tests pass, including a real multi-threaded concurrency test
(`test_concurrent_reservation_only_one_unit_available`) that spins up two threads with
separate DB connections racing to reserve the last unit of stock.

## Architecture

See [`docs/architecture-diagram.md`](docs/architecture-diagram.md) for the full diagram.
In short:

```
Routes (app/routers)  →  parse/validate HTTP, enforce RBAC, call ONE service function
Dependencies (app/dependencies.py)  →  JWT auth, role checks, warehouse-level authorization
Services (app/services)  →  ALL business logic, transaction boundaries, row locking
Models (app/models)  →  SQLAlchemy ORM + DB constraints (CHECK/UNIQUE/FK) as a backstop
```

Business rules live exclusively in the service layer. Routers never talk to the
database directly and never contain conditional business logic beyond RBAC dependency
wiring — this keeps the state-machine/locking/transaction logic testable independent of
HTTP and reusable across endpoints (e.g. both order cancellation and the return workflow
call the same `inventory_service` functions).

## Database design

See [`docs/er-diagram.md`](docs/er-diagram.md) for the full ER diagram and design notes.

Highlights:
- `inventory` is one row per `(product_id, warehouse_id)`, with a `UNIQUE` constraint and
  `CHECK` constraints preventing negative or impossible quantities.
- `inventory_transactions` is an append-only ledger — every stock change writes an
  immutable transaction row with `previous_quantity`/`new_quantity`, so history is always
  reconstructible independent of the current `inventory` row.
- `order_items.unit_price` is snapshotted at order-creation time and never rewritten,
  so historical orders are unaffected by later product price changes.
- `audit_logs` and `inventory_transactions` have no update/delete path anywhere in the
  service layer — they are immutable by construction, not just by convention.

## Concurrency strategy

This is the part of the assignment this implementation cares most about getting right.
The canonical race:

> User A orders Product X. User B orders Product X. Only one unit is available.

**Approach: PostgreSQL row-level locking (`SELECT ... FOR UPDATE`), not a distributed
lock.** Every function in `app/services/inventory_service.py` that mutates an `Inventory`
row starts by acquiring an exclusive row lock on that specific `(product_id,
warehouse_id)` row:

```python
stmt = (
    select(Inventory)
    .where(Inventory.product_id == product_id, Inventory.warehouse_id == warehouse_id)
    .with_for_update()
)
inventory = db.execute(stmt).scalar_one_or_none()
```

Postgres blocks a second transaction from acquiring the same row lock until the first
transaction commits or rolls back. So for the User A / User B race:

1. Both requests start a DB transaction and both call `reserve_inventory()`.
2. Whichever transaction reaches the `FOR UPDATE` select first gets the lock; the other
   blocks (it does **not** proceed with a stale read).
3. The winner checks `available_quantity >= requested_quantity`, decrements it, commits,
   releasing the lock.
4. The loser's `SELECT ... FOR UPDATE` then unblocks, reads the **post-commit** (now
   correct, lower) `available_quantity`, and correctly raises
   `InsufficientInventoryError` if there isn't enough left.

This is exercised directly in `tests/test_inventory.py::test_concurrent_reservation_only_one_unit_available`,
which uses two real threads with two separate SQLAlchemy sessions/connections (not
mocks) contending for the last unit of stock. Exactly one succeeds, one fails, and final
inventory state is verified to be exactly `available=0, reserved=1` — never negative,
never double-reserved.

**Layered on top of row locking:**

- **CHECK constraints** at the DB level (`available_quantity >= 0`,
  `reserved_quantity >= 0`, `reserved_quantity <= available_quantity + reserved_quantity`)
  are a hard backstop against negative/impossible inventory even if application logic
  has a bug — the database itself refuses the write.
- **Single transaction per business operation.** Service functions call `db.flush()`
  internally but never `db.commit()` — the caller (a router, or a composing service
  like `order_service.create_order()` reserving several line items in a loop) commits
  exactly once at the end. A crash or exception mid-operation rolls back everything:
  no partial order, no partial reservation.
- **Deterministic lock ordering for transfers.** `transfer_inventory()` always locks the
  lower `warehouse_id` first, regardless of transfer direction, so two transfers moving
  stock in opposite directions between the same warehouse pair can never deadlock.
- **Idempotency keys** solve a different problem row-locking doesn't: a *sequential*
  client retry of the same logical request (e.g. a network timeout after the server
  already committed) must not re-run the side effect. See [Idempotency](#idempotency).

**Why row locking over a Redis distributed lock:** for this platform's scale, DB-native
transactional locking is simpler to reason about (it participates directly in the same
transaction as the data it protects, so there's no separate lock-expiry/lock-leak failure
mode to manage) and is strictly consistent, whereas a distributed lock adds an extra
moving part and its own correctness edge cases (expiry vs. operation duration, etc.) for
a benefit that only shows up at much higher write contention. `SELECT ... FOR UPDATE SKIP
LOCKED` is a natural next step if lock-wait latency ever becomes a bottleneck.

## Authentication & authorization

- **JWT access tokens** (short-lived, 15 min default) + **refresh tokens** (7 days
  default), both signed with `HS256`.
- **Refresh token rotation**: every `/api/auth/refresh` call revokes the presented
  refresh token and issues a brand-new one. Reusing an already-rotated (or logged-out)
  refresh token is rejected — this both blocks replay and lets the system detect token
  theft (a revoked token being reused signals compromise).
  Test: `test_refresh_token_rotation`.
- **Password change** revokes all of that user's outstanding refresh tokens.
- **Inactive users** cannot authenticate at all (checked in `get_current_user`), and
  inactive fulfillment agents cannot receive order assignments.
- **Role-based authorization**: `app/dependencies.py::require_roles(*roles)` is used as a
  FastAPI dependency on every mutating endpoint.
- **Warehouse-level authorization**: `assert_warehouse_access()` checks the
  `warehouse_users` join table — admins may access any warehouse; warehouse managers and
  fulfillment agents are restricted to warehouses they're explicitly linked to.
- **Resource-based authorization**: customers can only view/cancel their own orders and
  returns (`assert_can_view_order`, checks in `order_service`/`return_service`).

## Order state machine

```
pending → confirmed → processing → packed → shipped → delivered → returned
   ↓          ↓            ↓          ↓
cancelled  cancelled   cancelled  cancelled
   ↓          ↓
 failed     failed
```

Enforced centrally in `app/models/order.py::ORDER_TRANSITIONS` (a dict of allowed
`from_status -> {to_statuses}`), checked in
`order_service.transition_order_status()`. An invalid transition (e.g.
`delivered → processing`) raises `INVALID_ORDER_STATUS` (HTTP 409). Every transition
writes an `OrderStatusHistory` row.

Cancellation is only permitted while the order is in `pending`, `confirmed`, or
`processing` (see `settings.CANCELLABLE_STATUSES`); cancelling releases all reserved
inventory, writes an audit log, and notifies the customer — all inside one transaction.

Shipping an order converts its reservations into permanent sales (inventory is removed
from `reserved_quantity` rather than returned to `available_quantity`).

## Idempotency

`app/services/idempotency_service.py` implements idempotency for order creation and
inventory adjust/transfer. Strategy:

1. On first request with an `idempotency_key`, a placeholder row is inserted into
   `idempotency_keys` (unique on `user_id, endpoint, key`) **inside the same transaction**
   as the business operation, before the operation runs.
2. If a concurrent duplicate request races in, the unique constraint causes the second
   insert to fail with `IntegrityError`, which is translated to
   `DuplicateIdempotencyKeyError` (409) — the caller should treat this as "already being
   processed" and retry the read.
3. Once the business operation completes, the response body is stored against that key.
4. Any later request with the same key returns the **stored response** directly, without
   re-running the operation — verified in
   `test_order_idempotent_creation`/`test_inventory_adjust_idempotency`, which assert
   inventory is not double-reserved/double-adjusted on retry.

## Error format

All errors return a consistent envelope:

```json
{
  "success": false,
  "error": {
    "code": "INSUFFICIENT_INVENTORY",
    "message": "Insufficient inventory for product SKU-1001"
  }
}
```

Domain error codes (see `app/exceptions.py`): `INSUFFICIENT_INVENTORY`,
`INVALID_ORDER_STATUS`, `INVALID_RETURN_STATUS`, `WAREHOUSE_ACCESS_DENIED`,
`PRODUCT_INACTIVE`, `DUPLICATE_IDEMPOTENCY_KEY`, `INVALID_INVENTORY_TRANSFER`,
`DUPLICATE_SKU`, plus generic `NOT_FOUND` / `UNAUTHORIZED` / `FORBIDDEN` /
`VALIDATION_ERROR` / `INTERNAL_SERVER_ERROR`.

## API overview

Interactive Swagger/OpenAPI docs at `/docs` once running. A ready-to-import
[`postman_collection.json`](postman_collection.json) covers every endpoint below with
example request bodies.

| Area | Endpoints |
|---|---|
| Auth | `POST /api/auth/{register,login,refresh,logout,change-password}`, `GET /api/auth/me` |
| Products | `POST/GET /api/products`, `GET/PUT /api/products/{id}`, `PATCH /api/products/{id}/{activate,deactivate}` |
| Warehouses | `POST/GET /api/warehouses`, `GET/PUT /api/warehouses/{id}`, `PATCH /api/warehouses/{id}/{activate,deactivate}` |
| Inventory | `GET /api/inventory`, `GET /api/inventory/low-stock`, `GET /api/inventory/{product_id}`, `POST /api/inventory/{adjust,transfer}` |
| Orders | `POST/GET /api/orders`, `GET /api/orders/{id}`, `POST /api/orders/{id}/cancel`, `PATCH /api/orders/{id}/status`, `POST /api/orders/{id}/assign` |
| Returns | `POST /api/returns`, `GET /api/returns/{id}`, `PATCH /api/returns/{id}/status` |
| Notifications | `GET /api/notifications`, `PATCH /api/notifications/{id}/read`, `PATCH /api/notifications/read-all` |
| Dashboard | `GET /api/dashboard/{admin,customer}`, `GET /api/dashboard/warehouse/{id}` |

Filtering/pagination example:

```
GET /api/orders?page=1&page_size=20&status=processing&warehouse_id=3
GET /api/inventory?low_stock=true
```

## Testing

47 tests across 5 files, run with `pytest -v` against a real Postgres database:

- `test_auth.py` (14): registration, login (success/invalid/inactive), token
  expiry/rotation/reuse-rejection, logout revocation, password change, RBAC denial,
  warehouse-access denial.
- `test_products.py` (6): CRUD, duplicate SKU, not-found, activate/deactivate,
  pagination.
- `test_inventory.py` (13): adjust (incl. negative-result rejection), idempotent adjust,
  transfer (incl. insufficient-stock rejection and unchanged-quantities-on-failure),
  low-stock filtering, **and the real two-thread concurrent-reservation race**.
- `test_orders.py` (14): creation + total calculation, insufficient inventory, inactive
  product, reservation side-effects, idempotent creation, resource-based authorization,
  full valid state-machine walk, invalid transitions (incl.
  `delivered → processing`), cancellation + inventory release, cancellation cutoff,
  assignment (incl. inactive-agent rejection).
- `test_returns.py` (6): creation, approval, rejection, invalid transitions,
  inventory restock on receipt, over-quantity rejection.

## Project layout

```
app/
  main.py              FastAPI app + router registration
  config.py            Settings (env-driven)
  database.py          Engine/session/Base
  security.py          Password hashing, JWT creation/verification
  dependencies.py       Auth, RBAC, warehouse-access dependencies
  exceptions.py        Domain error hierarchy + centralized handlers
  models/               SQLAlchemy models (one file per bounded concept)
  schemas/              Pydantic request/response schemas
  services/              Business logic (the layer that matters most - see above)
  routers/               Thin HTTP routing layer
alembic/                Migrations (env.py wired to app settings/models)
tests/                  Pytest suite (real Postgres, no mocks for DB behavior)
docs/                   ER diagram + architecture diagram (Mermaid)
docker-compose.yml, Dockerfile
postman_collection.json
```

## What's not implemented / trade-offs

Being upfront about scope, given the 5-7 day suggested timeline:

- **Background jobs / Celery / Redis**: notifications are written synchronously to the
  `notifications` table inside the same transaction as the triggering business event
  (guaranteeing consistency), rather than dispatched to a queue. Real delivery
  (email/push/websocket) would hang off `FastAPI BackgroundTasks` or a Celery worker
  reading unread notifications — the data model already supports this without
  migration. Low-stock notification generation on reorder-threshold breach is not yet
  wired up as an automatic trigger (the `GET /api/inventory/low-stock` query is
  implemented and correct; a scheduled job to proactively notify is the remaining bit).
- **Docker**: provided (`Dockerfile` + `docker-compose.yml`) but not the primary
  development path used while building this — Postgres was run locally for faster
  iteration on migrations/tests.
- **Rate limiting, API versioning, Prometheus metrics, CSV export**: listed as optional
  bonus features in the spec and not implemented, to prioritize correctness of the core
  concurrency/transaction/business-rule requirements instead.
- **WebSocket notifications**: not implemented; polling `GET /api/notifications` is
  the current mechanism.
