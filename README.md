# SkyMart Fraud Detection System

## Overview

Real-time fraud detection system for SkyMart Indonesia's e-commerce platform. The system ingests transaction events, evaluates them against a configurable rule engine, computes composite risk scores, persists fraud alerts in a database, and exposes results through a REST API consumed by an analyst dashboard.

Designed as a production-ready prototype that can scale from SQLite to PostgreSQL, from CSV batch ingestion to Kafka streaming, without architectural changes.

## Architecture

```
CSV / Streaming Feed
        |
        v
  +-----------------+
  |  Ingestion      |  src/pipeline/ingestion.py
  |  (validate,     |  - Field coercion
  |   deduplicate)  |  - Timestamp normalisation
  +-----------------+
        |
        v
  +-----------------+
  |  Rule Engine    |  src/pipeline/rules_engine.py
  |  (5 rules)      |  - Stateless evaluation
  +-----------------+  - Returns triggered rule labels
        |
        v
  +-----------------+
  |  Risk Scorer    |  src/pipeline/risk_scorer.py
  |  (weighted sum) |  - Clamps to [0, 100]
  +-----------------+
        |
        v
  +-----------------+
  |  Alert Storage  |  src/models/database.py
  |  (SQLite WAL)   |  - Transaction table
  +-----------------+  - FraudAlert if score >= 70
        |
        v
  +-----------------+
  |  REST API       |  src/api/routes/
  |  (FastAPI)      |  - GET/PATCH alerts
  +-----------------+  - GET metrics
        |              - WebSocket streaming
        v
  +-----------------+
  |  Dashboard      |  src/dashboard/index.html
  |  (Analyst UI)   |  - Charts, alert queue
  +-----------------+  - Real-time updates via WS
```

## Quick Start

### Prerequisites

- Python 3.11+
- pip

### Installation

```bash
pip install -r requirements.txt
```

### Generate Test Data

```bash
python data/generate_data.py
```

This produces `data/transactions.json` with 550+ synthetic transactions containing five embedded fraud patterns.

### Run the Pipeline

```bash
python scripts/run_pipeline.py
```

Reads `data/transactions.json`, runs each transaction through the rule engine and risk scorer, stores results in `fraud_detection.db`.

### Start the Dashboard

```bash
uvicorn src.api.main:app --reload
```

Open [http://localhost:8000](http://localhost:8000) to access the fraud analyst dashboard.

## Fraud Detection Logic

### Rules Implemented

| Rule | Label | Trigger Condition | Score Delta |
|------|-------|-------------------|-------------|
| Velocity Check | `VELOCITY` | More than 3 transactions from the same email within 10 minutes | +30 |
| High-Value First Purchase | `HIGH_VALUE_FIRST_PURCHASE` | First-time buyer with amount exceeding $1,000 | +35 |
| Multiple Declines | `MULTIPLE_DECLINES` | 3+ declined transactions from the same email in 1 hour, followed by an approval | +25 |
| Geographic Mismatch | `GEOGRAPHIC_MISMATCH` | Billing country differs from shipping country | +20 |
| Unusual Quantity | `UNUSUAL_QUANTITY` | Quantity exceeds 5 for high-value electronics (LAPTOP, SMARTPHONE, CAMERA) | +15 |

### Risk Score Calculation

The system uses an additive scoring model:

```
risk_score = min(sum(score_delta for each triggered rule), 100)
```

A `FraudAlert` record is created when `risk_score >= 70` (the configurable threshold). Alerts start in `NEEDS_REVIEW` status and can be transitioned to `INVESTIGATED`, `CONFIRMED_FRAUD`, or `CLEARED` by an analyst.

## Test Dataset

### Overview

The data generator (`data/generate_data.py`) produces 550+ transactions across a 24-hour window (2024-01-15) spanning:

- 5 countries: ID (Indonesia), SG, MY, TH, PH
- 4 payment methods: CREDIT_CARD (45%), GOPAY (25%), OVO (20%), BANK_TRANSFER (10%)
- 4 product categories: LAPTOP (30%), SMARTPHONE (35%), CAMERA (15%), ACCESSORIES (20%)
- Status distribution: 80% APPROVED, 10% SOFT_DECLINED, 10% HARD_DECLINED

### Embedded Fraud Patterns

| Pattern | Description | Count | Expected Detection |
|---------|-------------|-------|-------------------|
| Velocity Attacks | 8 attackers, each with 4-5 rapid transactions within 5 minutes | 32-40 txns | VELOCITY rule triggers |
| High-Value First Purchases | 12 new customers spending $1,100-2,400 | 12 txns | HIGH_VALUE_FIRST_PURCHASE triggers |
| Decline-then-Approve | 8 attackers with 3 declines followed by 1 approval | 32 txns | MULTIPLE_DECLINES triggers on the final approval |
| Geographic Mismatches | 8 transactions billing from SG/MY/TH, shipping to ID | 8 txns | GEOGRAPHIC_MISMATCH triggers |
| BIN Clusters | 6 suspicious BINs, each used by 3-4 different emails within 2 hours | 18-24 txns | Visible in top BINs analytics |

## Tech Stack

| Component | Technology | Purpose |
|-----------|------------|---------|
| API Framework | FastAPI 0.115 | Async REST API with automatic OpenAPI docs |
| Database | SQLite + aiosqlite | Async-compatible embedded database (WAL mode) |
| ORM | SQLAlchemy 2.0 (async) | Type-annotated database access |
| Validation | Pydantic v2 | Request/response schema validation |
| Configuration | pydantic-settings | Environment-variable-driven config |
| Dashboard | HTML + Chart.js 4.4 | Single-page analyst interface |
| Real-time | WebSocket (FastAPI) | Live alert streaming to dashboard |
| Test Data | Faker + stdlib | Synthetic transaction generation |
| Server | Uvicorn | ASGI server |

## Project Structure

```
SkyMart-Fraud-Detection/
|-- data/
|   |-- generate_data.py          # Synthetic data generator
|   |-- transactions.json         # Generated output (550+ transactions)
|
|-- src/
|   |-- __init__.py
|   |-- config.py                 # Centralised settings (pydantic-settings)
|   |
|   |-- api/
|   |   |-- __init__.py
|   |   |-- websocket.py          # WebSocket connection manager
|   |   |-- routes/
|   |       |-- __init__.py
|   |       |-- alerts.py         # Alert CRUD endpoints
|   |
|   |-- dashboard/
|   |   |-- index.html            # Analyst dashboard (self-contained)
|   |
|   |-- models/
|   |   |-- __init__.py
|   |   |-- database.py           # ORM models (Transaction, FraudAlert)
|   |
|   |-- pipeline/
|   |   |-- __init__.py
|   |   |-- rules_engine.py       # 5 fraud detection rules
|   |   |-- risk_scorer.py        # Weighted score aggregation
|   |
|   |-- schemas/
|       |-- __init__.py
|       |-- schemas.py            # Pydantic request/response models
|
|-- scripts/
|   |-- run_pipeline.py           # Batch pipeline runner
|
|-- requirements.txt              # Python dependencies
|-- ARCHITECTURE.md               # Detailed architecture document
|-- README.md                     # This file
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/transactions` | Ingest a single transaction |
| GET | `/api/alerts` | List alerts (filterable by status, min_risk, hours) |
| GET | `/api/alerts/{id}` | Get a single alert with its transaction |
| PATCH | `/api/alerts/{id}` | Update alert status |
| GET | `/api/transactions/{id}/related` | Related transactions by email, IP, BIN |
| GET | `/api/metrics` | Aggregate dashboard metrics |
| WS | `/ws/alerts` | Real-time alert stream |

## Configuration

All settings can be overridden via environment variables or a `.env` file:

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `sqlite+aiosqlite:///./fraud_detection.db` | Async database DSN |
| `RISK_SCORE_THRESHOLD` | `70` | Minimum score to create a FraudAlert |
| `VELOCITY_WINDOW_MINUTES` | `10` | Lookback for velocity rule |
| `DECLINE_WINDOW_HOURS` | `1` | Lookback for decline rule |
| `VELOCITY_MAX_TRANSACTIONS` | `3` | Max transactions before velocity triggers |
| `HIGH_VALUE_THRESHOLD` | `1000.0` | USD amount for high-value rule |
| `UNUSUAL_QUANTITY_THRESHOLD` | `5` | Quantity for unusual-qty rule |
