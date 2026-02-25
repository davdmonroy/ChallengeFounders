"""Metrics aggregation endpoints for the fraud detection dashboard."""
from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models.database import FraudAlert, Transaction, get_db
from src.schemas.schemas import MetricsResponse

logger = logging.getLogger(__name__)

metrics_router = APIRouter(prefix="/api/metrics", tags=["metrics"])


def _build_risk_buckets(alerts: list[FraudAlert]) -> list[dict[str, int | str]]:
    """Build risk score distribution across 10-point buckets.

    Args:
        alerts: List of fraud alert ORM objects.

    Returns:
        List of dicts with 'bucket' label and 'count' for each range.
    """
    bucket_counts: dict[str, int] = {}
    bucket_labels = [
        "0-9", "10-19", "20-29", "30-39", "40-49",
        "50-59", "60-69", "70-79", "80-89", "90-100",
    ]
    for label in bucket_labels:
        bucket_counts[label] = 0

    for alert in alerts:
        score = alert.risk_score
        if score >= 90:
            bucket_counts["90-100"] += 1
        else:
            idx = score // 10
            bucket_counts[bucket_labels[idx]] += 1

    return [{"bucket": label, "count": bucket_counts[label]} for label in bucket_labels]


def _compute_top_rules(alerts: list[FraudAlert], top_n: int = 10) -> list[dict[str, int | str]]:
    """Unnest triggered_rules JSON arrays and count occurrences.

    Args:
        alerts: List of fraud alert ORM objects.
        top_n: Number of top rules to return.

    Returns:
        List of dicts with 'rule' name and 'count', sorted descending.
    """
    rule_counter: Counter[str] = Counter()
    for alert in alerts:
        rules = alert.triggered_rules
        if isinstance(rules, str):
            try:
                rules = json.loads(rules)
            except (json.JSONDecodeError, TypeError):
                continue
        if isinstance(rules, list):
            for rule in rules:
                if isinstance(rule, str):
                    rule_counter[rule] += 1

    return [
        {"rule": rule, "count": count}
        for rule, count in rule_counter.most_common(top_n)
    ]


def _compute_hourly_volume(
    alerts: list[FraudAlert],
    hours: int,
) -> list[dict[str, int | str]]:
    """Group alerts by hour and fill in zeros for empty hours.

    Args:
        alerts: List of fraud alert ORM objects.
        hours: Number of hours to cover in the lookback window.

    Returns:
        List of dicts with 'hour' (ISO format) and 'count' per hour.
    """
    now = datetime.now(timezone.utc)
    hourly_counts: Counter[str] = Counter()

    for alert in alerts:
        ts = alert.created_at
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        hour_key = ts.strftime("%Y-%m-%dT%H:00:00Z")
        hourly_counts[hour_key] += 1

    result: list[dict[str, int | str]] = []
    for i in range(hours, 0, -1):
        hour_dt = now - timedelta(hours=i)
        hour_key = hour_dt.strftime("%Y-%m-%dT%H:00:00Z")
        result.append({"hour": hour_key, "count": hourly_counts.get(hour_key, 0)})

    return result


@metrics_router.get("", response_model=MetricsResponse)
async def get_metrics(
    hours: int = Query(default=24, ge=1, description="Lookback window in hours"),
    db: AsyncSession = Depends(get_db),
) -> MetricsResponse:
    """Compute aggregated fraud detection metrics.

    Provides hourly alert volume, risk score distribution, top triggered rules,
    and top suspicious entities (emails, IPs, card BINs) within the lookback window.

    Args:
        hours: Number of hours to look back from now.
        db: Async database session dependency.

    Returns:
        Aggregated metrics response covering all fraud dimensions.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    # Fetch all alerts within the window with their transactions eagerly loaded
    stmt = (
        select(FraudAlert)
        .options(selectinload(FraudAlert.transaction))
        .where(FraudAlert.created_at >= cutoff)
        .order_by(FraudAlert.created_at.desc())
    )
    result = await db.execute(stmt)
    alerts: list[FraudAlert] = list(result.scalars().all())

    # 1. Hourly alert volume
    hourly_alert_volume = _compute_hourly_volume(alerts, hours)

    # 2. Risk score distribution
    risk_score_distribution = _build_risk_buckets(alerts)

    # 3. Top triggered rules (Python-side aggregation from JSON arrays)
    top_triggered_rules = _compute_top_rules(alerts, top_n=10)

    # 4. Top suspicious emails
    email_counter: Counter[str] = Counter()
    ip_counter: Counter[str] = Counter()
    bin_counter: Counter[str] = Counter()

    for alert in alerts:
        txn = alert.transaction
        if txn is not None:
            if txn.customer_email:
                email_counter[txn.customer_email] += 1
            if txn.customer_ip:
                ip_counter[txn.customer_ip] += 1
            if txn.card_bin:
                bin_counter[txn.card_bin] += 1

    top_suspicious_emails = [
        {"email": email, "count": count}
        for email, count in email_counter.most_common(5)
    ]

    # 5. Top suspicious IPs
    top_suspicious_ips = [
        {"ip": ip, "count": count}
        for ip, count in ip_counter.most_common(5)
    ]

    # 6. Top suspicious BINs
    top_suspicious_bins = [
        {"card_bin": card_bin, "count": count}
        for card_bin, count in bin_counter.most_common(5)
    ]

    # 7. Total alerts in last 24h
    total_alerts_24h = len(alerts) if hours == 24 else 0
    if hours != 24:
        cutoff_24h = datetime.now(timezone.utc) - timedelta(hours=24)
        count_stmt = (
            select(func.count())
            .select_from(FraudAlert)
            .where(FraudAlert.created_at >= cutoff_24h)
        )
        count_result = await db.execute(count_stmt)
        total_alerts_24h = count_result.scalar() or 0

    # 8. High risk alerts (risk_score >= 80)
    high_risk_alerts = sum(1 for a in alerts if a.risk_score >= 80)

    return MetricsResponse(
        hourly_alert_volume=hourly_alert_volume,
        risk_score_distribution=risk_score_distribution,
        top_triggered_rules=top_triggered_rules,
        top_suspicious_emails=top_suspicious_emails,
        top_suspicious_ips=top_suspicious_ips,
        top_suspicious_bins=top_suspicious_bins,
        total_alerts_24h=total_alerts_24h,
        high_risk_alerts=high_risk_alerts,
    )
