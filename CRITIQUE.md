# SkyMart Fraud Detection System — Devil's Advocate Critique

**Reviewed by:** Engineering Devil's Advocate
**Date:** 2026-02-25
**Codebase:** `/src/pipeline/`, `/src/api/routes/`, `/src/models/database.py`, `/src/config.py`
**Scope:** Architecture, fraud logic, operational readiness, and test methodology

---

## Preamble

This critique is written to make the system better, not to dismiss the work. The team has built a functional, readable, and well-structured prototype in Python 3.11 + FastAPI. The goal here is to be brutally honest about what breaks in production, what a real fraudster exploits, and what the metrics actually mean. Every issue identified below has a concrete mitigation path.

---

## 1. Fraud Rule Weaknesses (Critical)

### Rule 1 — VELOCITY (`rules_engine.py`, lines 56–101)

**False positive risk:**
A customer refreshing their cart on a slow connection can trigger 4+ transaction attempts in 10 minutes entirely legitimately — especially with BNPL (buy-now-pay-later) flows that retry on network timeout. A family sharing one email account (common in Indonesia and SEA markets) placing simultaneous orders for different products during a sale event will trip this rule. Flash sale events like Harbolnas (12.12) create legitimate velocity spikes that look identical to fraud bursts. The rule will fire on legitimate customers at exactly the moment the business most needs to approve orders.

**Evasion:**
This is trivially bypassed. A professional fraud ring uses one email address per card, sourced from free disposable email providers (Mailinator, Guerrilla Mail, 10minutemail). The rule fires on `customer_email` only — `rules_engine.py` line 82 — so distributing across emails renders it completely blind. A fraudster running 50 cards with 50 email addresses generates zero velocity signal while processing 50 fraudulent transactions in parallel.

**Edge case:**
The velocity query on line 83 includes the transaction being evaluated because the row is flushed to the database at `ingestion.py` line 108 (`await session.flush()`) *before* the rule is evaluated at line 111. This means the count returned will always be at least 1 even for a brand new customer. The trigger threshold is `count > VELOCITY_MAX_TRANSACTIONS` (strictly greater than 3), so in practice the rule fires when 5 or more rows exist for that email in the window, not 4. This is an off-by-one that means the stated behavior ("more than 3 transactions") is actually "more than 4 transactions including the current one." Document this or fix the flush ordering.

---

### Rule 2 — HIGH_VALUE_FIRST_PURCHASE (`rules_engine.py`, lines 103–137)

**False positive risk:**
New-to-platform customers purchasing a laptop as a gift are the canonical legitimate use case here. The rule does not distinguish between a stolen card and a genuine first purchase. In Indonesia, consumers buying their first-ever e-commerce purchase often choose high-value electronics precisely because online prices are substantially lower than retail — this is the purchase that most needs to go through, and most likely to be blocked.

**Evasion:**
The rule reads `is_first_purchase` directly from the incoming transaction payload (`rules_engine.py` line 122). This field is sourced from the upstream system — it is not computed by this system from purchase history. A fraudster using a stolen account that has existing purchase history will have `is_first_purchase=False`, and this rule will never fire regardless of the amount. The rule only catches stolen cards used on genuinely new accounts, which is a fraction of account-takeover fraud. Accounts with prior purchase history are entirely invisible.

**Edge case:**
There is no relationship between `amount_usd` and `unit_price * quantity`. A transaction with `amount_usd=1500`, `unit_price=300`, `quantity=5`, `is_first_purchase=True` is scored identically to `amount_usd=1500`, `unit_price=1500`, `quantity=1`. These represent very different risk profiles — a bulk order of moderate-priced items vs. a single ultra-high-value item — but produce the same +35 score delta. The rule treats all amounts above $1000 as equivalent regardless of composition.

---

### Rule 3 — MULTIPLE_DECLINES (`rules_engine.py`, lines 139–197)

**False positive risk:**
A legitimate customer whose card was compromised and reissued may have a prior string of declines (the compromised card being declined by their bank) followed by a successful transaction on the new card. Notably, `customer_email` is the join key (line 173), not `card_bin`. If the customer's new card succeeds, this rule fires on them — the victim of fraud gets flagged as the fraudster. Additionally, customers who genuinely mistype their CVV multiple times before succeeding (common on mobile checkout) will accumulate SOFT_DECLINED records and trigger this rule on their eventual successful purchase.

**Evasion:**
Fraudsters who are card-testing (probing stolen BINs with micro-transactions to check validity) keep their decline rate low by abandoning a card after 1-2 declines rather than 3. Since the threshold is `declined_count >= 3` (line 180), stopping at 2 declines per email completely avoids this rule. A sophisticated ring tests each card with a single transaction, discards declined cards, and only submits the validated card for the high-value fraud attempt — generating zero decline history.

**Edge case:**
The rule query on lines 169–178 counts SOFT_DECLINED and HARD_DECLINED statuses but does not count PENDING or TIMED_OUT states. If the upstream payment processor returns PENDING transactions that eventually resolve as declined, those are invisible to this rule. The 1-hour window (`settings.DECLINE_WINDOW_HOURS`) is also misaligned: card-testing operations typically run over minutes, not hours. A fraudster who tests 3 cards over 90 minutes — two declines before the 1-hour cutoff, one after — evades the rule.

---

### Rule 4 — GEOGRAPHIC_MISMATCH (`rules_engine.py`, lines 199–230)

**False positive risk:**
This rule has the highest false positive rate in the set. The following are all common legitimate scenarios that trigger it:
- Expatriates in Indonesia purchasing goods to send home (billing=ID, shipping=PH/MY/SG)
- Business travelers buying locally but shipping to their home country
- Corporate procurement using a company billing address in one country with delivery to an employee in another
- Gift purchases for overseas relatives (Indonesian diaspora is large)
- Digital nomads with a home-country card shipping to their current country

In a Southeast Asian e-commerce context, cross-border shipping is extremely common. For SkyMart Indonesia specifically, billing_country=ID with shipping_country=SG or MY is a routine transaction pattern during promotional events. This rule will flag a significant percentage of legitimate international orders.

**Evasion:**
Fraudsters who know this rule exists use a reshipping service (freight forwarder) in the same country as the stolen card's billing address, making billing and shipping countries match. The package is then forwarded to the final destination. The rule fires on legitimate international buyers while professional fraud operations using reshipping services are completely invisible.

**Edge case:**
Country codes are compared with `!=` (line 217) against raw string values stored by the upstream system. There is no normalization. If the upstream sends `"indonesia"` for one transaction and `"ID"` for another, the comparison is unreliable. There is also no handling for territories and dependencies — a transaction billing to `"US"` and shipping to `"PR"` (Puerto Rico) triggers this rule despite being a domestic US transaction. The schema (`database.py` lines 145–154) documents these as ISO 3166-1 alpha-2 but performs no validation that they actually are.

---

### Rule 5 — UNUSUAL_QUANTITY (`rules_engine.py`, lines 232–271)

**False positive risk:**
Corporate procurement is the primary victim here. An IT department ordering 6 laptops for new hires, or a photography business buying 6 cameras for an event, is routine B2B behavior that looks identical to a fraud bulk order from the rule's perspective. The rule fires on any quantity > 5 for LAPTOP/SMARTPHONE/CAMERA with no consideration of whether the account is a business account, whether there is a prior purchase history, or whether the per-unit price matches normal retail pricing.

**Evasion:**
Split the order. A fraudster wanting to purchase 10 laptops submits 2 orders of 5 units each, neither of which exceeds the threshold of `> 5`. If velocity detection is also in play, they space the orders more than 10 minutes apart. The combination of these two simple evasions — split orders + time gaps — defeats both VELOCITY and UNUSUAL_QUANTITY simultaneously.

**Edge case:**
The category list is hardcoded to `{"LAPTOP", "SMARTPHONE", "CAMERA"}` (line 250), stored as a Python set in the rule function itself rather than in `settings` or `config.py`. Adding TABLET or SMARTWATCH to the monitored categories requires a code change and redeployment, not a configuration change. This also means any case variation in the incoming `product_category` field (e.g., `"laptop"` instead of `"LAPTOP"`) silently causes the rule to never trigger.

---

### The Additive Scoring Problem (`risk_scorer.py`, lines 51–88)

The scoring model is linear and additive: each rule contributes a fixed delta with no interaction terms and no contextual weighting. This creates two structural failures:

**Score inflation from independent signals:**
GEOGRAPHIC_MISMATCH (+20) + VELOCITY (+30) = 50 points. These are independent signals that may have unrelated root causes — a legitimate expat customer who also happens to buy during a high-traffic flash sale. Neither individually suggests fraud, but their sum approaches the alert threshold. The system treats coincident independent signals as multiplicatively suspicious when they may simply be correlated with a demographic profile (e.g., expat online shoppers tend to be higher-income and buy more during sales).

**No cap per risk dimension:**
All 5 rules firing = 30 + 35 + 25 + 20 + 15 = 125, capped at 100. But this means the scoring ceiling is reached only when all 5 rules fire, and the cap masks the actual signal strength. A transaction at score 100 from rules {VELOCITY + HIGH_VALUE_FIRST + MULTIPLE_DECLINES} looks identical on the dashboard to one scoring 100 from all 5 rules, even though the latter is substantially more suspicious.

**Calibration basis is unknown:**
The score deltas (30, 35, 25, 20, 15) and the alert threshold (70) appear nowhere in the codebase with a comment explaining their derivation. They are not derived from historical false positive rates, precision-recall curves, or expected value calculations. The threshold of 70 was presumably chosen to ensure the test data generates a reasonable-looking alert rate. In production with real data, these numbers may produce 90% false positives or miss 60% of actual fraud — there is no way to know without empirical calibration.

---

## 2. Architecture Weaknesses (Serious)

### SQLite Concurrency (`database.py`, lines 50–76)

WAL mode allows concurrent readers, but SQLite still enforces a single-writer lock. At 50,000 transactions/day, the average is 0.58 writes/second, which SQLite handles trivially. The problem is the peak distribution. E-commerce transaction volumes are not uniform — they are highly bursty:

- During promotional events (12.12, 11.11, Lebaran sales), burst rates of 50-100 tx/second are realistic
- The ingestion pipeline opens a new session per transaction (`ingestion.py` line 220: `async with async_session() as session`) and calls `await session.commit()` on every row (line 149)
- Each commit is a full fsync in WAL mode
- At 50 tx/second with per-transaction commits, the write queue will stall

The breaking point is not the average load — it is the burst. A single-writer SQLite database with per-row commits will exhibit write latency degradation well before 10 transactions/second sustained. The ARCHITECTURE.md mentions batch inserts (section 3.1) but `ingestion.py` processes transactions one-by-one in a sequential loop (lines 218-234) with individual commits. There are no batch inserts in the actual implementation.

### True Real-Time vs. Simulated (`ingestion.py`, lines 153–243)

The system is described as "real-time fraud detection" in the architecture document and dashboard. It is not. The ingestion path is:

1. Load entire JSON file into memory (`line 176: json.load(fh)`)
2. Process transactions sequentially in a for-loop (line 218)
3. Apply an artificial `delay_seconds` sleep between transactions (lines 233-234)

This is batch processing with a configurable delay — not stream processing. The fraud detection latency is the time for a transaction to reach the front of the ingestion queue, which depends on how many transactions precede it and what the artificial delay is set to. If `delay_seconds=0` and 550 transactions are being processed, the last transaction in the file is evaluated 549 transactions after it was "submitted." If a fraudster's transaction lands at position 500 in a batch of 550, the detection latency is measured in minutes, not milliseconds.

The actual fraud window — the time between a fraudulent transaction occurring and an alert being raised — is undefined and potentially unbounded. A fraud operation that completes its full attack within the batch processing window of a single pipeline run will not trigger real-time countermeasures.

### No Transaction Deduplication at the Pipeline API Level

While `process_transaction` in `ingestion.py` (lines 90-98) does implement a per-transaction duplicate check at the database level, the pipeline trigger endpoint behavior is not idempotent at the batch level. If `POST /api/pipeline/trigger` is called twice with the same data source, the duplicate check prevents double-insertion of transactions, but:

- The `FraudDetectionPipeline` instance's `processed_count` and `flagged_count` (lines 61-62) are instance-level counters that accumulate across runs
- The summary returned by the endpoint will show different totals on the second run (all transactions skipped) vs. the first run, without a clear indication that the run was a no-op
- If the pipeline is long-running and a second trigger arrives mid-run, two pipeline instances can be executing concurrently against the same database, with both issuing `session.flush()` calls for different transactions — potential write conflicts

### In-Memory WebSocket State (`websocket.py`, lines 18-19)

The `ConnectionManager` stores active WebSocket connections in a plain Python list: `self.active_connections: list[WebSocket] = []`. This state is entirely process-local. Consequences:

- On any application restart (crash, deployment, OOM kill), all connected dashboard clients are silently disconnected with no reconnection message
- If alerts were generated during the downtime, dashboards that were connected before the restart will never receive them — they are not backfilled
- There is no mechanism to replay missed alerts to a reconnected client; the client must manually refresh
- In a multi-worker deployment (`gunicorn -k uvicorn.workers.UvicornWorker` as described in ARCHITECTURE.md section 7.4), each worker process has its own `ConnectionManager` instance. A WebSocket connected to worker A will not receive alerts generated by a pipeline running in worker B. The broadcast architecture is fundamentally incompatible with multi-process deployment.

### Missing Idempotency on Alert Creation (`ingestion.py`, lines 117-135)

The deduplication check (lines 90-98) prevents duplicate `Transaction` rows, but there is no corresponding uniqueness constraint on `FraudAlert`. The database schema (`database.py` lines 217-279) defines `alert_id` as a UUID primary key but has no UNIQUE constraint on `transaction_id` in the `fraud_alerts` table. If a race condition or bug causes `process_transaction` to be called twice for the same transaction before the first call commits, two `FraudAlert` rows will be created for the same transaction. This would cause the alert to appear twice in the analyst queue with different UUIDs, inflating alert counts and creating analyst confusion.

---

## 3. Risk Scoring Model Problems (Important)

### Score Calibration Basis

The following score combinations cross the alert threshold of 70:

| Combination | Score | Threshold Crossed |
|---|---|---|
| VELOCITY + HIGH_VALUE_FIRST | 30 + 35 = 65 | No (just below) |
| VELOCITY + GEOGRAPHIC_MISMATCH + MULTIPLE_DECLINES | 30 + 20 + 25 = 75 | Yes |
| HIGH_VALUE_FIRST + MULTIPLE_DECLINES | 35 + 25 = 60 | No |
| VELOCITY + HIGH_VALUE_FIRST + GEOGRAPHIC_MISMATCH | 30 + 35 + 20 = 85 | Yes |
| GEOGRAPHIC_MISMATCH + MULTIPLE_DECLINES + UNUSUAL_QUANTITY | 20 + 25 + 15 = 60 | No |

The threshold of 70 is a step function: VELOCITY + GEOGRAPHIC_MISMATCH + MULTIPLE_DECLINES produces an alert at 75; removing any one of those three rules produces scores of 55, 50, or 45 — all safely below threshold. Whether 75 represents a genuinely alert-worthy transaction is unknowable without baseline false positive data. No A/B testing, no precision-recall analysis, no expected value calculation anchors these numbers.

### No Statistical Baseline

All rule thresholds are hardcoded constants with no documented empirical basis:

- `VELOCITY_MAX_TRANSACTIONS = 3` — why 3? Is 3 transactions in 10 minutes actually anomalous for SkyMart's customer base? During a flash sale, legitimate customers may submit 4-5 attempts due to stock race conditions.
- `HIGH_VALUE_THRESHOLD = 1000.0` — is $1000 meaningfully above the average order value? If SkyMart's AOV is $200, then yes. If it's $600, the threshold needs recalibration.
- `UNUSUAL_QUANTITY_THRESHOLD = 5` — derived from intuition, not from the distribution of quantity in historical orders.

Without knowing the 95th percentile of transaction velocity, the 99th percentile of order value, or the distribution of quantities per category in legitimate orders, these thresholds are guesses. The system cannot quantify its own false positive rate.

### Missing ML Component

Even within the constraint of a 2-hour prototype, a session-level anomaly signal would substantially improve detection quality. An Isolation Forest trained on {`amount_usd`, `quantity`, `unit_price`, velocity-count, is_first_purchase} with the 550 synthetic transactions as training data would produce a continuous anomaly score that catches compound patterns invisible to rule thresholds. The rules catch known patterns; ML catches unknown ones.

For example: a transaction with `amount_usd=999.99` (just below the $1000 HIGH_VALUE_FIRST threshold), `quantity=5` (just below the UNUSUAL_QUANTITY threshold of > 5), and `is_first_purchase=True` scores 0 points from all 5 rules. It is at the boundary of every threshold but triggers none. A simple Isolation Forest would likely flag it as anomalous. The system as designed is blind to near-miss compound patterns.

### Threshold Sensitivity

There is no tooling to answer the question: "What happens to alert volume if we change the threshold from 70 to 65?" Lowering the threshold by 5 points would add every transaction scoring 65-69 to the alert queue. Without knowing the distribution of scores in the range [65, 70], this change could double the alert volume or add 2% — the team cannot know without running an analysis. Any threshold tuning in production is a shot in the dark.

---

## 4. Dashboard and Operations Gaps (Moderate)

### No Authentication

`PATCH /api/alerts/{alert_id}` (alerts.py, lines 102-145) accepts status updates from any caller with no authentication, no API key check, no session cookie, and no identity assertion. Any person with network access to the server can clear fraud alerts, confirm legitimate transactions as fraud, or flip alert statuses to any valid state. This is not just a security problem — it is a compliance problem. PCI DSS requires audit trails for fraud investigation decisions. There is no `reviewed_by` field in the `FraudAlert` model, no record of who made a status change, and no timestamp of when an individual analyst made a decision (only `updated_at` which records the last write time without any actor attribution).

### Alert Acknowledgment Race Condition (`alerts.py`, lines 127-143)

The `update_alert_status` endpoint reads the alert, modifies it, and commits in a non-atomic sequence:

```python
result = await db.execute(stmt)       # line 133 — reads current state
alert = result.scalar_one_or_none()   # line 134
alert.alert_status = body.alert_status  # line 139 — modifies in memory
await db.commit()                     # line 142 — writes
```

There is no pessimistic lock (`SELECT FOR UPDATE`), no optimistic concurrency check (no `version` or `etag` field), and no uniqueness constraint preventing concurrent updates. If two analysts open the same alert simultaneously and both submit status changes, the last write wins silently. The first analyst's decision is overwritten with no conflict notification. In a busy operations center with multiple analysts working the same alert queue, this is a real operational problem that produces incorrect audit records.

### No SLA Tracking

The `FraudAlert` model has `created_at` and `updated_at` fields but no `first_reviewed_at` field and no SLA deadline. There is no dashboard metric showing "alerts pending review for more than X minutes," no escalation mechanism for aged alerts, and no notification when an analyst's review takes too long. The fraud window remains open while an alert sits in the queue. A transaction flagged at 2:00 AM that is not reviewed until 9:00 AM represents 7 hours of potential exposure — the system has no way to represent or report on this.

### Missing Chargeback Feedback Loop

The `CONFIRMED_FRAUD` status in `VALID_STATUSES` (alerts.py, line 23) records an analyst's belief that fraud occurred, but there is no mechanism to record the downstream outcome — whether a chargeback was actually filed, whether the bank confirmed the fraud, and whether the original alert was a true positive or a false positive based on the ultimate outcome. This means the system can never self-evaluate. The precision and recall of the model are permanently unknowable because ground truth is never recorded.

### Dashboard Performance — GET /api/metrics (`metrics.py`, lines 111-205)

The metrics endpoint loads all alerts within the lookback window into Python memory (line 138: `list(result.scalars().all())`), eagerly loads all related transactions via `selectinload` (line 134), and then performs all aggregations in Python (`_build_risk_buckets`, `_compute_top_rules`, `_compute_hourly_volume`). At 50K transactions/day with a 10% flagging rate (based on the synthetic data distribution), a 24-hour window would load ~5,000 `FraudAlert` rows plus 5,000 `Transaction` rows into memory for every single dashboard page load. This is an N+1 query by another name: one query to load all alerts, plus implicit sub-queries for all their related transactions. All aggregations should be pushed into SQL `GROUP BY` queries rather than performed in Python.

---

## 5. Test Data Bias (Methodological)

### Known Fraud Labels — The System Is Confirming, Not Detecting

The test data is generated by Faker with fraud patterns pre-constructed to match the 5 rules. When the system runs and correctly flags those transactions, it is demonstrating that it can find patterns it was told to find in data that was designed to contain those patterns. This is not detection — it is pattern matching on a known answer key. The metrics (precision, recall, F1) derived from this test run measure how well the rules find the patterns the data generator built in, which is trivially 100% when the rules and the data generation are aligned.

A real production validation would require: (a) historical labeled transaction data, (b) a holdout set the model has never seen, and (c) evaluation on naturally occurring fraud patterns rather than synthetic ones constructed to trigger specific rules.

### Distribution Mismatch

Real-world e-commerce fraud rates are typically 0.1% to 0.5% of transaction volume (industry consensus for Southeast Asian markets). The synthetic data at 8-10% fraud rate produces metrics that look excellent in testing but will not reproduce in production. Specifically:

- **Precision** will drop substantially: if the model was calibrated on 10% fraud prevalence but production has 0.3% prevalence, the same threshold will produce many more false positives than true positives even at the same rule configuration
- **The alert queue will be overwhelmed**: scaling the 10% rate to 50K/day = 5,000 alerts/day. At 0.3% actual fraud rate, only 150 of those are real — 4,850 are false positives that analysts must process and clear daily

The synthetic data's inflated fraud rate is the single most dangerous source of bias in the prototype metrics because it creates false confidence in alert volumes that will not match production.

### No Adversarial Patterns

All fraud in the test data appears to be single-rule triggers — transactions designed to fire exactly one or two rules cleanly. Real fraud operations use compound patterns where each individual signal is below the threshold. For example: `amount_usd=999` (below $1000), `quantity=5` (below >5), `billing_country=ID`, `shipping_country=ID` (no geo mismatch), `is_first_purchase=False` (stolen account with history). This transaction scores 0 points across all 5 rules and sails through undetected. The test data does not contain this class of adversarial transaction.

### Missing Edge Cases in Test Data

The Faker-generated data likely does not include:

- **International corporate cards**: billing_country != the issuing bank's country — triggers GEOGRAPHIC_MISMATCH for legitimate B2B buyers
- **Recurring subscriptions**: a customer with a monthly subscription will have high velocity in the 10-minute window on renewal day if multiple subscription charges are batched
- **Family accounts**: parents and children sharing one email address for a family plan or household account
- **Payment retries**: payment processor retrying a timed-out transaction creates legitimate duplicates with different transaction IDs but identical content
- **Currency conversion edge cases**: transactions where `amount_usd` is calculated from IDR with rounding, producing values like $1000.0001 that technically exceed the threshold

---

## 6. Production Readiness Gaps (Critical for Real Deployment)

### No Rate Limiting

Every API endpoint — including `POST /api/pipeline/trigger` — has no rate limiting. A single client can call `POST /api/pipeline/trigger` in a tight loop, spawning multiple overlapping pipeline instances that each try to ingest the same data. The duplicate check in `process_transaction` prevents duplicate rows but does not prevent the compute and I/O overhead of processing duplicates through the rule engine. An unintentional or malicious triggering loop could saturate SQLite's write capacity and take down the entire service.

### Silent Pipeline Failures

The `_ingest` method (ingestion.py, lines 200-243) catches no exceptions at the transaction level. If `process_transaction` raises an unhandled exception (database timeout, malformed field, unexpected None value), the exception propagates out of the for-loop at line 218, terminates the entire ingestion run, and leaves partially processed batches with no record of which transactions were completed. The caller receives a 500 error from the API; there is no alert, no retry queue, no dead-letter storage for the failed transactions. Partial batch processing is invisible to operators.

### Unbounded Database Growth

There is no data retention policy for the `transactions` table. Every ingested transaction is stored indefinitely. At 50K transactions/day, the table grows by roughly 50,000 rows daily. SQLite imposes no practical storage limit beyond filesystem capacity, but query performance on unindexed columns degrades as the table grows. The velocity rule query (`rules_engine.py` lines 78-86) uses a time-bounded WHERE clause and the `customer_email` index, which should remain fast — but `GET /api/metrics` loads entire time windows into memory (metrics.py line 138) and will become progressively slower as historical data accumulates.

### No Backup or Recovery for SQLite

The entire system state — all transactions, all alerts, all analyst decisions — is stored in a single local SQLite file (`fraud_detection.db`). There is no backup script, no point-in-time recovery, no offsite copy. A disk failure, an accidental `rm`, or a corrupted WAL file loses all data permanently. The ARCHITECTURE.md acknowledges this as a prototype tradeoff but provides no interim mitigation (e.g., a nightly `cp` of the `.db` file to a backup location).

### Timezone Handling — Naive vs. Aware Datetimes

The codebase mixes timezone-aware and timezone-naive datetime objects in ways that will cause silent bugs:

- `ingestion.py` line 104: `datetime.fromisoformat(raw_ts)` — if the ISO string has no timezone suffix (e.g., `"2024-01-15T10:30:00"`), this produces a naive datetime that is stored in SQLite without timezone information
- `rules_engine.py` line 74: `datetime.now(timezone.utc)` — produces a timezone-aware datetime
- The WHERE clause comparison on line 84: `Transaction.timestamp >= cutoff` — compares a timezone-aware cutoff against potentially timezone-naive stored timestamps

In SQLite, datetime comparisons are string-based. A naive datetime `"2024-01-15T10:30:00"` and an aware datetime `"2024-01-15T10:30:00+00:00"` will not compare equal and string-based `>=` ordering may be incorrect depending on the formatting. The `_compute_hourly_volume` function in metrics.py (lines 97-98) explicitly handles the naive case with `ts.replace(tzinfo=timezone.utc)`, acknowledging that naive timestamps exist in the database — but the rules engine does not apply this same normalization, meaning the velocity window query may silently return incorrect counts for transactions ingested with naive timestamps.

---

## 7. What's Actually Good (Fairness)

The team built real things correctly. These strengths are worth preserving and building on:

**asyncio end-to-end is the right call.** The choice to use SQLAlchemy 2.0 async + aiosqlite + FastAPI native async means that when the concurrency model needs to evolve, the transition path (replacing aiosqlite with asyncpg, swapping the ingestion loop for a Kafka consumer) involves configuration and driver changes, not architectural rewrites. The async abstraction is correctly layered.

**SQLite with WAL is acceptable for this scale.** 50K transactions/day at 0.6/second average does not require PostgreSQL. The WAL configuration in `database.py` lines 50-76 is correctly applied via the `connect` event listener rather than a one-time PRAGMA call, ensuring every connection enables WAL rather than relying on a single session to set it. This is the right implementation pattern.

**The deduplication check exists and is correctly placed.** `process_transaction` in `ingestion.py` lines 90-98 runs the duplicate check before any other processing, before flushing the transaction, and uses the primary key index for a fast lookup. This is the right place for it and the right implementation.

**The 5 rules cover the highest-frequency fraud vectors for SEA e-commerce.** Velocity fraud, card testing (MULTIPLE_DECLINES), and unusual quantity bulk orders are the top three fraud patterns by volume in Indonesian e-commerce according to public industry data. The rule selection is defensible even if the implementation details need work.

**The drill-down endpoint is operationally excellent.** `GET /api/transactions/{id}/related` (`transactions.py` lines 48-128) returning related transactions by email, IP, and card BIN simultaneously gives an analyst three investigation pivots in a single API call. This is exactly the right data for fraud investigation workflows and reflects genuine operational thinking.

**Pydantic v2 + FastAPI is a solid, modern foundation.** The use of `model_config = ConfigDict(from_attributes=True)` for ORM serialization, `pydantic-settings` for configuration management, and the clean separation of request validation from business logic is well-executed. The stack choice will not become a liability.

**`RuleResult` as a frozen dataclass is a good design decision.** The immutability of `RuleResult` (`rules_engine.py` lines 30-44) means rule evaluation results cannot be mutated downstream. This makes the scoring pipeline deterministic and easy to test in isolation.

---

## 8. Prioritized Recommendations

Prioritized by impact-to-effort ratio, with specific implementation guidance for each.

---

### Priority 1 — Add UNIQUE constraint on `fraud_alerts.transaction_id` (High impact, 5 minutes)

**Problem:** A bug or race condition can produce two `FraudAlert` rows for one transaction (discussed in Section 2).
**Fix:** Add `unique=True` to the `transaction_id` mapped column in `database.py` and add a unique index. Also add `__table_args__` with `UniqueConstraint("transaction_id")` to `FraudAlert`.
**Why first:** Data integrity. Every other problem is less critical than preventing duplicate alerts in the queue.

---

### Priority 2 — Add API key authentication to mutation endpoints (High impact, 30 minutes)

**Problem:** `PATCH /api/alerts/{alert_id}` and `POST /api/pipeline/trigger` are unauthenticated and accessible to any caller (Section 4).
**Fix:** Add an `X-API-Key` header dependency using FastAPI's `Security` and `HTTPBearer`, compare against a `API_SECRET_KEY` setting in `config.py`. Apply as a dependency to mutation routes only. Add a `reviewed_by: str | None` field to `AlertStatusUpdate` schema so analyst identity is recorded on every status change.
**Why second:** Compliance and audit trail. The system is useless for PCI DSS purposes without knowing who made which decision.

---

### Priority 3 — Normalize country codes and add case-insensitive matching to GEOGRAPHIC_MISMATCH (High impact, 15 minutes)

**Problem:** The geographic mismatch rule (`rules_engine.py` line 217) compares raw strings with no normalization, producing incorrect results for non-canonical country code formats (Section 1, Rule 4).
**Fix:** Apply `.upper().strip()` to both `billing_country` and `shipping_country` before comparison. Add a Pydantic validator on the `Transaction` ingestion schema that enforces ISO 3166-1 alpha-2 format (exactly 2 uppercase letters). This fixes both the comparison bug and the false positive rate for legitimate cross-border orders that would previously be mis-detected due to format differences.

---

### Priority 4 — Derive velocity threshold from the actual transaction data distribution (Medium impact, 1 hour)

**Problem:** `VELOCITY_MAX_TRANSACTIONS = 3` is an arbitrary constant with no empirical basis (Section 3).
**Fix:** Before running the fraud pipeline, compute the 99th percentile of transaction counts per email per 10-minute window over the full synthetic dataset. Set `VELOCITY_MAX_TRANSACTIONS` to that percentile value. Add a one-time analysis script (`scripts/calibrate_thresholds.py`) that outputs recommended values for all configurable thresholds. Document the derivation in a comment next to each constant in `config.py`.

---

### Priority 5 — Add Isolation Forest anomaly score as a 6th signal (Medium impact, 1.5 hours)

**Problem:** The rule engine is blind to near-miss compound patterns where every individual feature is below threshold but the combination is anomalous (Section 3).
**Fix:** Train a `sklearn.ensemble.IsolationForest` on the numeric features `[amount_usd, quantity, unit_price]` plus derived features `[velocity_count, is_first_purchase_int]` using the ingested transaction history. Produce a continuous `anomaly_score` in [0, 1]. Add this as a 6th signal to `ScoreResult` (not to `RuleResult` — keep it separate to maintain rule interpretability). Use it as a secondary alert flag: transactions that score < 70 on the rule-based system but have `anomaly_score > 0.8` are surfaced to analysts in a separate "anomaly review" queue, not conflated with rule-based alerts.

---

### Priority 6 — Add a `chargeback_outcome` field and feedback recording endpoint (Medium impact, 1 hour)

**Problem:** The system cannot compute its own precision or recall because ground truth is never recorded (Section 4).
**Fix:** Add `chargeback_outcome: str | None` and `chargeback_amount_usd: float | None` to the `FraudAlert` model. Add `POST /api/alerts/{alert_id}/chargeback` to record the downstream outcome when a chargeback is confirmed by the bank. This single change enables the team to compute true positive rate, false positive rate, and dollar value saved — the three metrics that matter to the business.

---

### Priority 7 — Implement batch commits in the ingestion pipeline (Medium impact, 45 minutes)

**Problem:** Per-transaction commits (`ingestion.py` line 149) will cause write stalls under burst load (Section 2).
**Fix:** Accumulate transactions into batches of N (configurable, suggested N=50) and commit once per batch. The deduplication check can be batched with `IN` queries. The `processed_count` and `flagged_count` counters should be updated per-batch rather than per-transaction. This change reduces the number of fsync operations by a factor of N without changing the observable behavior.

---

### Priority 8 — Migrate to PostgreSQL + message queue for production (Low immediate impact, 1-2 days)

**Problem:** SQLite single-writer and in-process pipeline cannot scale beyond the prototype (Sections 2 and 6).
**Path:** The `DATABASE_URL` in `config.py` line 44 is already designed for this swap — changing it to a `postgresql+asyncpg://` DSN is a one-line change. The incremental production path is:
1. **Step 1 (day 1):** Swap to PostgreSQL, add `pool_size=10` and `max_overflow=20` to `create_async_engine`. No other code changes required.
2. **Step 2 (week 1):** Replace the `POST /api/pipeline/trigger` batch endpoint with a Kafka consumer. The `ingest_from_list` method signature is already compatible — wrap it in an `aiokafka` consumer loop.
3. **Step 3 (week 2):** Move WebSocket state to Redis Pub/Sub to support multi-worker deployments.

Each step is independently deployable and does not require the subsequent step to be complete.

---

## Summary Table

| Issue | Severity | Effort | Priority |
|---|---|---|---|
| No UNIQUE constraint on `fraud_alerts.transaction_id` | Critical | 5 min | 1 |
| No authentication on mutation endpoints | Critical | 30 min | 2 |
| Country code normalization missing | High | 15 min | 3 |
| Arbitrary rule thresholds, no calibration | High | 1 hr | 4 |
| No ML anomaly signal for compound patterns | Medium | 1.5 hr | 5 |
| No chargeback feedback loop | Medium | 1 hr | 6 |
| Per-transaction commits under burst load | Medium | 45 min | 7 |
| Velocity rule: email-only, trivially evaded | High | Design | — |
| Geographic mismatch: high legitimate false positive rate | High | Config | — |
| Test data fraud rate 10x higher than production | High | Methodology | — |
| In-memory WebSocket state lost on restart | Medium | Design | — |
| Timezone naive/aware mixing | Medium | 30 min | — |
| No rate limiting on pipeline trigger | Medium | 15 min | — |
| SQLite unbounded growth, no retention policy | Low-Med | Config | — |
| No SLA tracking on alert review time | Low | Design | — |

---

*The goal of this critique is not to suggest the prototype is wrong — it is to ensure the team understands exactly where the prototype ends and production begins, and to prioritize the highest-leverage improvements before the system processes real customer data.*
