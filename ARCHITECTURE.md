# SkyMart Fraud Detection System — Architecture Document

## 1. Overview

This document describes the architectural design of the SkyMart real-time fraud detection
system. The system ingests e-commerce transaction events, evaluates them against a
configurable rule engine, computes a composite risk score, persists fraud alerts, and
exposes the results via a REST API consumed by an analyst dashboard.

---

## 2. System Architecture Diagram

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                         SKYMART FRAUD DETECTION SYSTEM                           │
└──────────────────────────────────────────────────────────────────────────────────┘

  External Source
  ─────────────
  CSV / Streaming
  Transaction Feed
        │
        │  (transaction payload)
        ▼
┌───────────────────┐
│  INGESTION LAYER  │   src/pipeline/ingestion.py
│                   │   ─ Reads raw CSV rows
│  - CSV loader     │   ─ Validates field types
│  - Field coercion │   ─ Normalises timestamps to UTC
│  - Deduplication  │   ─ Skips duplicate transaction_id
└────────┬──────────┘
         │
         │  TransactionCreate (Pydantic-validated)
         ▼
┌───────────────────┐
│   RULE ENGINE     │   src/pipeline/rules.py
│                   │   ─ Stateless, pure functions
│  Rules evaluated  │   ─ Each rule returns triggered flag
│  in sequence:     │     + human-readable label
│                   │
│  1. VELOCITY      │   > VELOCITY_MAX_TRANSACTIONS txns
│  2. HIGH_VALUE    │     in VELOCITY_WINDOW_MINUTES
│  3. GEO_MISMATCH  │   > HIGH_VALUE_THRESHOLD USD
│  4. DECLINE_RATE  │   Billing country ≠ shipping country
│  5. UNUSUAL_QTY   │   Multiple declines in
│  6. FIRST_PURCHASE│     DECLINE_WINDOW_HOURS
│                   │   Quantity > UNUSUAL_QUANTITY_THRESHOLD
└────────┬──────────┘   First purchase with high value
         │
         │  list[str]  — triggered rule labels
         ▼
┌───────────────────┐
│   RISK SCORER     │   src/pipeline/scorer.py
│                   │   ─ Receives list of triggered rules
│  Rule weights:    │   ─ Sums weighted scores
│  VELOCITY    25   │   ─ Clamps result to [0, 100]
│  HIGH_VALUE  20   │   ─ Returns integer risk_score
│  GEO_MISMATCH 20  │
│  DECLINE_RATE 25  │
│  UNUSUAL_QTY 15   │
│  FIRST_PURCHASE10 │
└────────┬──────────┘
         │
         │  risk_score: int
         ▼
┌───────────────────┐
│  ALERT STORAGE    │   src/models/database.py
│                   │   ─ Persists Transaction ORM row
│  SQLite (WAL)     │   ─ If risk_score >= THRESHOLD:
│  ─ transactions   │     creates FraudAlert ORM row
│  ─ fraud_alerts   │   ─ Async writes via SQLAlchemy 2.0
│                   │     aiosqlite driver
└────────┬──────────┘
         │
         │  ORM rows → Pydantic response models
         ▼
┌───────────────────┐
│    REST API       │   src/api/routes/
│                   │   ─ POST /transactions
│  FastAPI async    │   ─ GET  /alerts
│  endpoints        │   ─ GET  /alerts/{id}
│                   │   ─ PATCH /alerts/{id}/status
│  Pydantic I/O     │   ─ GET  /transactions/{id}/related
│  validation       │   ─ GET  /metrics
│  OpenAPI docs     │
└────────┬──────────┘
         │
         │  JSON over HTTP
         ▼
┌───────────────────┐
│    DASHBOARD      │   src/dashboard/
│                   │   ─ Analyst-facing UI
│  Alert queue      │   ─ Visualises risk score
│  Metrics charts   │     distribution, alert volume,
│  Manual review    │     top suspicious entities
│  workflow         │   ─ Calls PATCH endpoint to update
│                   │     alert_status
└───────────────────┘
```

---

## 3. Component Descriptions

### 3.1 Ingestion Layer (`src/pipeline/ingestion.py`)

Responsible for reading transaction data from the CSV feed, coercing raw string values
into typed Python objects, deduplicating by `transaction_id`, and emitting
`TransactionCreate` Pydantic instances to the downstream rule engine.

Key responsibilities:
- Parse ISO-8601 timestamps, normalising timezone-naive values to UTC.
- Coerce numeric fields (`amount_usd`, `unit_price`, `quantity`) to their expected types.
- Skip rows with a `transaction_id` that already exists in the database to guarantee
  idempotency on repeated runs.
- Batch inserts to reduce per-row round-trips to SQLite.

### 3.2 Rule Engine (`src/pipeline/rules.py`)

A collection of pure, stateless evaluation functions. Each rule accepts the current
transaction and a view of recent history (queried from the database) and returns a
boolean together with a canonical rule label string.

| Rule            | Label             | Description                                            |
|-----------------|-------------------|--------------------------------------------------------|
| Velocity check  | `VELOCITY`        | > N transactions from the same email in M minutes     |
| High value      | `HIGH_VALUE`      | Single transaction amount exceeds configured threshold |
| Geo mismatch    | `GEO_MISMATCH`    | Billing country differs from shipping country          |
| Decline rate    | `DECLINE_RATE`    | Multiple declines from same email within 1 hour        |
| Unusual qty     | `UNUSUAL_QTY`     | Quantity ordered exceeds configured threshold          |
| First purchase  | `FIRST_PURCHASE`  | First purchase flagged together with high value        |

Rules are evaluated in parallel where possible and their labels are aggregated into a
list that is forwarded to the Risk Scorer.

### 3.3 Risk Scorer (`src/pipeline/scorer.py`)

Receives the list of triggered rule labels and returns a deterministic integer score in
the range [0, 100]. Each rule carries a pre-configured integer weight. The scorer sums
the weights of all triggered rules and clamps the result to 100.

```
score = min(sum(weights[rule] for rule in triggered_rules), 100)
```

This design keeps scoring logic decoupled from rule evaluation, making it straightforward
to tune weights via configuration without modifying rule code.

### 3.4 Alert Storage (`src/models/database.py`)

Async SQLAlchemy 2.0 layer backed by SQLite (WAL mode). Provides:
- `Transaction` ORM model — one row per ingested transaction.
- `FraudAlert` ORM model — created only when `risk_score >= RISK_SCORE_THRESHOLD`.
- `async_session` factory for use throughout the application.
- `get_db` async generator compatible with FastAPI's `Depends` injection system.

WAL (Write-Ahead Logging) mode is enabled at connection time so that concurrent readers
are never blocked by ongoing writes, which is critical for dashboard queries executing
while the ingestion pipeline is running.

### 3.5 REST API (`src/api/routes/`)

Built with FastAPI for its native `asyncio` support, automatic OpenAPI schema generation,
and first-class Pydantic integration. Endpoints:

| Method | Path                             | Purpose                                      |
|--------|----------------------------------|----------------------------------------------|
| POST   | `/transactions`                  | Ingest a single transaction synchronously    |
| GET    | `/alerts`                        | List fraud alerts (filterable, paginated)    |
| GET    | `/alerts/{alert_id}`             | Retrieve a single alert with transaction     |
| PATCH  | `/alerts/{alert_id}/status`      | Update review status of an alert             |
| GET    | `/transactions/{id}/related`     | Fetch related transactions by email/IP/BIN   |
| GET    | `/metrics`                       | Aggregate metrics for dashboard charts       |

All request and response bodies are validated by Pydantic schemas defined in
`src/schemas/schemas.py`.

### 3.6 Dashboard (`src/dashboard/`)

Analyst-facing interface. Communicates exclusively through the REST API. Displays:
- Real-time alert queue grouped by `alert_status`.
- Risk score distribution histogram.
- Hourly alert volume trend line.
- Top suspicious emails, IPs, and card BINs ranked by alert count.
- Per-alert detail view enabling manual status transitions.

---

## 4. Technology Choices and Rationale

### 4.1 SQLite with aiosqlite — Prototype Database

**Why not PostgreSQL?**
PostgreSQL would be the production choice. For this prototype, SQLite with the aiosqlite
async driver provides:

- **Zero infrastructure** — no server process, no connection pooling daemon, no
  environment-specific credentials beyond a file path.
- **Single-file portability** — the entire dataset ships as one `.db` file, simplifying
  handoff and reproducibility.
- **WAL mode concurrency** — SQLite in WAL mode supports one concurrent writer and
  multiple concurrent readers, which is sufficient for a prototype with a single ingestion
  process and a handful of API consumers.
- **SQLAlchemy 2.0 compatibility** — the same ORM models and query expressions work
  unchanged when the `DATABASE_URL` is swapped to a PostgreSQL DSN.

**Trade-offs accepted:**
- No horizontal write scaling.
- No native JSON indexing (JSON columns stored as TEXT).
- File locking constraints on network filesystems.

### 4.2 FastAPI

- **Native asyncio** — non-blocking I/O from HTTP request handling down to database
  queries via `async with session` without thread-pool overhead.
- **Automatic OpenAPI/Swagger UI** — interactive documentation generated from Pydantic
  type annotations at zero extra cost.
- **Pydantic v2 integration** — request validation, serialization, and response
  filtering are handled declaratively through schema classes.
- **Dependency injection** — `get_db` plugs directly into `Depends()`, scoping a
  database session to each request lifecycle cleanly.

### 4.3 asyncio Throughout

- SQLAlchemy 2.0 async engine (`create_async_engine`) + aiosqlite driver avoid blocking
  the event loop during I/O, allowing multiple concurrent HTTP requests to share a single
  OS thread.
- The ingestion pipeline uses `asyncio.gather` to parallelise rule evaluation where rules
  query the database independently.
- This architecture makes the step to a truly async message queue (Kafka consumer) a
  natural extension rather than a rewrite.

### 4.4 Pydantic v2

- **Performance** — v2's Rust-backed core (`pydantic-core`) is significantly faster than
  v1 for large batch validation during ingestion.
- **`model_config = ConfigDict(from_attributes=True)`** — enables direct ORM-to-schema
  serialization without manual mapping.
- **`pydantic-settings`** — `Settings` class reads from environment variables and `.env`
  files with the same validation guarantees as request schemas.

### 4.5 SQLAlchemy 2.0

- The 2.0 style (`select()`, `scalars()`, `execute()`) is fully type-annotated and works
  identically in sync and async contexts.
- `async_sessionmaker` provides a reusable session factory whose configuration (expire on
  commit, autoflush) is set once and inherited by every session.

---

## 5. Data Flow — Single Transaction End-to-End

```
Step 1  CSV row / POST body arrives at the Ingestion Layer.
        ↓
Step 2  TransactionCreate schema validates all fields.
        Raises 422 Unprocessable Entity on invalid input.
        ↓
Step 3  Rule Engine queries recent history for this email/IP:
          - COUNT of transactions in the last VELOCITY_WINDOW_MINUTES minutes.
          - COUNT of declines in the last DECLINE_WINDOW_HOURS hours.
        Evaluates all rules in sequence.
        Produces: triggered_rules = ["VELOCITY", "GEO_MISMATCH"]
        ↓
Step 4  Risk Scorer sums weights:
          VELOCITY(25) + GEO_MISMATCH(20) = 45
        risk_score = 45
        ↓
Step 5  Alert Storage:
          Always inserts a Transaction row.
          If risk_score >= RISK_SCORE_THRESHOLD (70):
            Inserts a FraudAlert row with alert_status = "NEEDS_REVIEW".
          If risk_score < threshold:
            No alert created; transaction stored for future velocity queries.
        ↓
Step 6  API returns TransactionResponse (or FraudAlertResponse if alert created).
        ↓
Step 7  Dashboard polls GET /alerts and displays new NEEDS_REVIEW entries.
        Analyst reviews context via GET /transactions/{id}/related.
        Analyst updates status via PATCH /alerts/{id}/status.
```

---

## 6. Database Schema Overview

### Table: `transactions`

| Column               | Type     | Constraints                    | Notes                          |
|----------------------|----------|--------------------------------|--------------------------------|
| transaction_id       | VARCHAR  | PRIMARY KEY                    | UUID string from source system |
| timestamp            | DATETIME | NOT NULL                       | UTC event time                 |
| customer_email       | VARCHAR  | NOT NULL, INDEX                | Used for velocity queries      |
| customer_ip          | VARCHAR  | NOT NULL, INDEX                | Used for IP-based lookups      |
| billing_country      | VARCHAR  | NOT NULL                       | ISO 3166-1 alpha-2             |
| shipping_country     | VARCHAR  | NOT NULL                       | ISO 3166-1 alpha-2             |
| card_bin             | VARCHAR  | INDEX, NULLABLE                | First 6 digits of card number  |
| payment_method       | VARCHAR  | NOT NULL                       | CREDIT_CARD/GOPAY/OVO/etc.     |
| amount_usd           | FLOAT    | NOT NULL                       | Transaction amount in USD      |
| status               | VARCHAR  | NOT NULL                       | APPROVED/SOFT_DECLINED/etc.    |
| product_category     | VARCHAR  | NOT NULL                       | LAPTOP/SMARTPHONE/etc.         |
| quantity             | INTEGER  | NOT NULL, DEFAULT 1            |                                |
| unit_price           | FLOAT    | NOT NULL                       |                                |
| device_fingerprint   | VARCHAR  | NULLABLE                       | Browser/device hash            |
| is_first_purchase    | BOOLEAN  | NOT NULL, DEFAULT FALSE        |                                |
| created_at           | DATETIME | SERVER DEFAULT now()           | Row insertion time             |

### Table: `fraud_alerts`

| Column           | Type     | Constraints                          | Notes                          |
|------------------|----------|--------------------------------------|--------------------------------|
| alert_id         | VARCHAR  | PRIMARY KEY                          | UUID string generated here     |
| transaction_id   | VARCHAR  | NOT NULL, FK → transactions          | Links alert to transaction     |
| risk_score       | INTEGER  | NOT NULL                             | 0–100 composite score          |
| triggered_rules  | JSON     | NOT NULL                             | Stored as JSON array of labels |
| alert_status     | VARCHAR  | NOT NULL, DEFAULT 'NEEDS_REVIEW'     | Review workflow state          |
| created_at       | DATETIME | SERVER DEFAULT now()                 | Alert creation time            |
| updated_at       | DATETIME | ON UPDATE now(), NULLABLE            | Last status change time        |

### Relationships

```
transactions (1) ──────── (0..1) fraud_alerts
                  transaction_id FK
```

A transaction may have zero or one fraud alert. An alert always references exactly one
transaction.

---

## 7. Scalability Considerations — Path to Production

The following changes convert this prototype into a production-grade deployment with
minimal architectural disruption due to the clean layer boundaries.

### 7.1 Database: SQLite → PostgreSQL

```python
# config.py change
DATABASE_URL: str = "postgresql+asyncpg://user:pass@host:5432/fraud_db"
```

- Replace `aiosqlite` with `asyncpg` driver.
- Add `pool_size`, `max_overflow`, `pool_timeout` to `create_async_engine`.
- Migrate WAL-mode event listener to a PG-specific `search_path` setter.
- Add GIN index on `triggered_rules` JSONB column for fast rule-based filtering.
- Add composite index on `(customer_email, timestamp)` for velocity window queries.

### 7.2 Caching: Redis

- Cache velocity counters (email → transaction count in window) in Redis with TTL equal
  to `VELOCITY_WINDOW_MINUTES * 60` seconds.
- Eliminates the most frequent repeated SQL aggregation query.
- Use `aioredis` to maintain non-blocking I/O.

### 7.3 Message Queue: Apache Kafka

- Replace the CSV ingestion loop with a Kafka consumer reading from a
  `transactions.raw` topic.
- The ingestion process becomes a consumer group member, enabling horizontal scaling
  by adding consumer instances.
- The rule engine publishes scored results to a `transactions.scored` topic.
- A separate alert writer service consumes `transactions.scored` and writes alerts,
  decoupling I/O from scoring CPU work.

### 7.4 Horizontal API Scaling

- Deploy multiple FastAPI instances behind a load balancer (nginx / AWS ALB).
- Session affinity is not required because all state lives in PostgreSQL.
- Use Gunicorn + Uvicorn workers (`gunicorn -k uvicorn.workers.UvicornWorker`).

### 7.5 Observability

- Structured JSON logging (stdlib `logging` + `python-json-logger`).
- Prometheus metrics endpoint (`/metrics` via `prometheus-fastapi-instrumentator`).
- Distributed tracing via OpenTelemetry with Jaeger or AWS X-Ray.
- Alert on p99 ingestion latency > 500 ms and rule engine error rate > 0.1%.

### 7.6 Security Hardening

- Migrate `card_bin` storage to an encrypted column (pgcrypto / SQLAlchemy encrypted
  type) to limit PCI DSS scope.
- Add OAuth2 / API key authentication to all REST endpoints.
- Enable TLS termination at the load balancer; internal traffic stays on VPC.
- Rotate the SQLite file path secret via AWS Secrets Manager or Vault.

---

## 8. Configuration Reference

All tuneable parameters are defined in `src/config.py` and can be overridden via
environment variables or a `.env` file:

| Variable                      | Default  | Description                                       |
|-------------------------------|----------|---------------------------------------------------|
| `DATABASE_URL`                | SQLite   | SQLAlchemy async DSN                              |
| `RISK_SCORE_THRESHOLD`        | 70       | Minimum score to create a FraudAlert              |
| `VELOCITY_WINDOW_MINUTES`     | 10       | Lookback window for transaction velocity rule     |
| `DECLINE_WINDOW_HOURS`        | 1        | Lookback window for decline rate rule             |
| `VELOCITY_MAX_TRANSACTIONS`   | 3        | Max allowed transactions before velocity triggers |
| `HIGH_VALUE_THRESHOLD`        | 1000.0   | USD amount above which HIGH_VALUE rule triggers   |
| `UNUSUAL_QUANTITY_THRESHOLD`  | 5        | Quantity above which UNUSUAL_QTY rule triggers    |
| `APP_TITLE`                   | SkyMart… | OpenAPI title                                     |
| `APP_VERSION`                 | 1.0.0    | OpenAPI version                                   |
