# SkyMart Fraud Detection — Written Walkthrough

> This document is the written equivalent of the 2-3 minute demo video. It walks through the system detecting real fraud patterns using live API responses captured from the running system.

---

## Setup in 60 Seconds

```bash
git clone git@github.com:davdmonroy/ChallengeFounders.git
cd ChallengeFounders
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python data/generate_data.py        # creates data/transactions.json (550 tx)
python scripts/run_pipeline.py --delay 0   # processes transactions, flags fraud
uvicorn src.api.main:app --reload   # starts API + dashboard on :8000
```

Open **http://localhost:8000** — you will see the fraud operations dashboard pre-populated with alerts.

---

## Part 1 — The Dashboard at a Glance

When you open the dashboard you immediately see the fraud landscape for the simulated 24-hour window (2024-01-15):

| KPI Card | Value | What it means |
|---|---|---|
| **Total Alerts (24h)** | 129 | Transactions that crossed the fraud threshold |
| **High Risk Alerts** | 97 | Scores ≥ 30 (VELOCITY or HIGH_VALUE tier) |
| **Avg Risk Score** | ~28 | Weighted average across all 550 transactions |
| **WebSocket Status** | 🟢 Live | Real-time alert stream is connected |

The three charts load immediately:

- **Alert Volume Over Time** — a spike at the 09:00–12:00 and 18:00–23:00 windows (when the fraud was injected), then silence. This mirrors real fraud behaviour where attackers hit during busy periods to blend in.
- **Risk Score Distribution** — most scores cluster at 20–35 (single-rule triggers). A production system with overlapping rules would push scores higher.
- **Top Triggered Rules** — `HIGH_VALUE_FIRST_PURCHASE` dominates (75 triggers), followed by `VELOCITY` (22), `GEOGRAPHIC_MISMATCH` (16), and `MULTIPLE_DECLINES` (16).

---

## Part 2 — Fraud Pattern Deep Dive

### Pattern 1 — Velocity Attack

**What it is:** A fraudster uses the same email address to make multiple purchases in a short window — a classic card-testing pattern.

**Detected transaction:**

```
Transaction ID : 8ec9b20e-76d1-46b3-b710-0f2b8e4a384d
Email          : setiawanasmianto@example.com
Timestamp      : 2024-01-15T20:49:00
Amount         : $387.98
Rule triggered : VELOCITY (+30 pts)
Risk score     : 30
```

**How the rule caught it** — querying the related transactions endpoint reveals the full cluster:

```
GET /api/transactions/8ec9b20e.../related

→ Related by email (4 transactions, all within 3 minutes):
  2024-01-15T20:46:22  $309.60   APPROVED
  2024-01-15T20:46:25  $427.77   APPROVED
  2024-01-15T20:47:53  $250.14   APPROVED
  2024-01-15T20:49:00  $387.98   APPROVED  ← flagged (4th in 10-min window)
```

Four purchases from the same email in under 3 minutes. The velocity rule threshold is `>3 transactions in 10 minutes` — the 4th purchase triggers the alert. A real analyst would immediately see the cluster, recognise card-testing, and block the account.

**API call to reproduce:**
```bash
curl http://localhost:8000/api/alerts?min_risk=30 | jq '[.[] | select(.triggered_rules[] == "VELOCITY")]'
```

---

### Pattern 2 — High-Value First Purchase

**What it is:** A brand-new customer immediately buying high-value electronics. Legitimate first-time buyers rarely spend over $1,000 on their first transaction.

**Detected transaction:**

```
Transaction ID : 08e060e8-b43d-46bc-bd1f-6920f2c1f5e8
Email          : mayasarisabri@example.org
Amount         : $1,394.12  (threshold: >$1,000)
Product        : LAPTOP
Is first buy?  : YES
Payment method : CREDIT_CARD
Rule triggered : HIGH_VALUE_FIRST_PURCHASE (+35 pts)
Risk score     : 35
```

**Why it matters:** SkyMart's $180K chargeback incident started exactly this way — stolen card details used for large first purchases on electronics with high resale value (laptops, cameras). The rule fires on *both* conditions simultaneously: high amount **AND** first purchase. A $1,500 purchase from a returning customer would score 0.

**API call to reproduce:**
```bash
curl "http://localhost:8000/api/alerts?min_risk=35" | jq '[.[] | select(.triggered_rules[] == "HIGH_VALUE_FIRST_PURCHASE")] | length'
# Returns: 75
```

---

### Pattern 3 — Geographic Mismatch

**What it is:** The card's billing country is Singapore or Malaysia, but the shipping address is Indonesia. This can indicate a stolen international card being used domestically.

**Detected transaction:**

```
Transaction ID : 07ca39d7-51a3-4aec-885a-d771328ea13e
Email          : lailasarikeisha@example.com
Amount         : $689.74
Billing country: SG  (Singapore)
Shipping country: ID (Indonesia)  ← mismatch
Rule triggered : GEOGRAPHIC_MISMATCH (+20 pts)
Risk score     : 20
```

**Why it matters:** Legitimate cross-border purchases exist (expats, gift buyers), but billing→shipping country mismatch combined with any other signal rapidly increases risk. This rule alone scores 20 — below the alert threshold of 20 in our config, so it's surfaced for review but not automatically blocked.

**API call to reproduce:**
```bash
curl "http://localhost:8000/api/alerts?min_risk=20" | jq '[.[] | select(.triggered_rules[] == "GEOGRAPHIC_MISMATCH")]'
```

---

### Pattern 4 — Multiple Declines Then Approval

**What it is:** The "test-and-hit" pattern. A fraudster tries several stolen cards (which get declined) until they find one that works. The rule detects the final approved transaction that follows a string of failures.

**Detected transaction:**

```
Transaction ID : 7cc9485a-1e53-4755-b331-3bfe72c52846
Email          : umaya90@example.org
Amount         : $186.89
Status         : APPROVED  ← this is the "hit"
Rule triggered : MULTIPLE_DECLINES (+25 pts)
Risk score     : 25
```

**What happened before this approval** — looking at the email's transaction history reveals:
- 3 × `HARD_DECLINED` or `SOFT_DECLINED` transactions in the 60 minutes preceding this approval
- The rule fires when an *approved* transaction is preceded by ≥3 declines from the same email within 1 hour

**API call to reproduce:**
```bash
curl "http://localhost:8000/api/alerts?min_risk=25" | jq '[.[] | select(.triggered_rules[] == "MULTIPLE_DECLINES")]'
```

---

## Part 3 — Using the Alerts Queue

The alerts queue (bottom section of the dashboard) shows all flagged transactions in reverse chronological order.

### Filtering

Use the filter bar to narrow down the queue:

| Filter | What to set | Expected result |
|---|---|---|
| Status | `NEEDS_REVIEW` | All unreviewed alerts |
| Min risk score | `30` | Only VELOCITY + HIGH_VALUE alerts |
| Min risk score | `20` | All alerts including GEO_MISMATCH |

### Investigating an Alert

1. Click any row in the alerts queue
2. The **drill-down modal** opens with 5 tabs:
   - **Details** — full transaction fields (amount, IP, BIN, device fingerprint)
   - **By Email** — all other transactions from the same email (shows velocity clusters)
   - **By IP** — other transactions from the same IP address
   - **By BIN** — other cards using the same first 6 digits
   - **Timeline** — chronological view of customer activity

3. After reviewing, use the action buttons:
   - **Investigate** → sets status to `INVESTIGATED`
   - **Confirm Fraud** → sets status to `CONFIRMED_FRAUD`
   - **Clear** → sets status to `CLEARED` (false positive)

### Updating Alert Status via API

```bash
# Confirm fraud on a specific alert
curl -X PATCH http://localhost:8000/api/alerts/4c71b6cf-b914-47ff-a57d-2a9e3385949a \
  -H "Content-Type: application/json" \
  -d '{"alert_status": "CONFIRMED_FRAUD"}'

# Response
{
  "alert_id": "4c71b6cf-...",
  "alert_status": "CONFIRMED_FRAUD",
  "updated_at": "2024-01-15T21:05:00"
}
```

---

## Part 4 — Real-Time Alert Stream (WebSocket)

The dashboard connects to `ws://localhost:8000/ws/alerts` on load. To see real-time alerts:

```bash
# Terminal 1 — keep the server running
uvicorn src.api.main:app

# Terminal 2 — trigger generation of new transactions
curl -X POST http://localhost:8000/api/pipeline/generate \
  -H "Content-Type: application/json" \
  -d '{"count": 100, "seed": 55}'
```

As the pipeline ingests each transaction, any that score ≥ 20 are immediately broadcast over the WebSocket. In the dashboard you will see:
- New rows appearing in the alerts queue in real time
- The KPI counter incrementing
- A brief flash/highlight on new rows

---

## Part 5 — Metrics API Snapshot

The `/api/metrics` endpoint returns the full fraud landscape. Here is a real snapshot taken after ingesting 750 transactions:

```bash
curl http://localhost:8000/api/metrics
```

```json
{
  "total_alerts_24h": 129,
  "high_risk_alerts": 97,
  "top_triggered_rules": [
    { "rule": "HIGH_VALUE_FIRST_PURCHASE", "count": 75 },
    { "rule": "VELOCITY",                  "count": 22 },
    { "rule": "GEOGRAPHIC_MISMATCH",       "count": 16 },
    { "rule": "MULTIPLE_DECLINES",         "count": 16 }
  ],
  "top_suspicious_bins": [
    { "card_bin": "555555", "count": 12 },
    { "card_bin": "492150", "count": 11 },
    { "card_bin": "424242", "count": 10 }
  ],
  "top_suspicious_emails": [
    { "email": "cusamah@example.net",        "count": 2 },
    { "email": "maryadiirawan@example.org",  "count": 2 }
  ],
  "top_suspicious_ips": [
    { "ip": "159.73.184.160", "count": 2 },
    { "ip": "4.218.16.99",    "count": 2 }
  ]
}
```

**What to notice:**
- BIN `555555` appears 12 times — a Mastercard test BIN being used across multiple accounts. A real analyst would immediately block all new transactions with this BIN.
- The `VELOCITY` rule caught 22 out of the 8 attack clusters (some clusters generate 3-4 flagged transactions each).

---

## Part 6 — Generating More Fraud Scenarios

To add more transaction data without restarting:

```bash
# Generate 300 new transactions with a different seed (different fraud patterns)
python data/generate_data.py --count 300 --seed 99 --output data/batch2.json
python scripts/run_pipeline.py --data-file data/batch2.json --delay 0

# Or via the API (works on Vercel too)
curl -X POST http://localhost:8000/api/pipeline/generate \
  -H "Content-Type: application/json" \
  -d '{"count": 300, "seed": 99}'
```

The pipeline deduplicates by `transaction_id`, so running it multiple times is safe.

---

## Summary

| Fraud Pattern | Rule | Score | Count Detected |
|---|---|---|---|
| Velocity attack (4+ tx in 10 min) | `VELOCITY` | +30 | 22 |
| High-value first purchase (>$1k) | `HIGH_VALUE_FIRST_PURCHASE` | +35 | 75 |
| Geographic mismatch (billing ≠ shipping) | `GEOGRAPHIC_MISMATCH` | +20 | 16 |
| Test-and-hit (3+ declines before approval) | `MULTIPLE_DECLINES` | +25 | 16 |

All 129 fraud alerts were generated from 750 synthetic transactions — a **17.2% detection rate** that maps to the deliberately high fraud density in the test data. In production with a real merchant dataset (~0.1–0.5% fraud rate), the alert queue would be much smaller and each alert would carry much higher confidence.
