"""Alert management endpoints for the fraud detection dashboard."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from src.models.database import FraudAlert, Transaction, get_db
from src.schemas.schemas import AlertStatusUpdate, FraudAlertResponse

logger = logging.getLogger(__name__)

alerts_router = APIRouter(prefix="/api/alerts", tags=["alerts"])

VALID_STATUSES: set[str] = {
    "NEEDS_REVIEW",
    "INVESTIGATED",
    "CLEARED",
    "CONFIRMED_FRAUD",
}


@alerts_router.get("", response_model=list[FraudAlertResponse])
async def get_alerts(
    status: str | None = Query(default=None, description="Filter by alert status"),
    min_risk: int = Query(default=0, ge=0, le=100, description="Minimum risk score"),
    hours: int = Query(default=24, ge=1, description="Lookback window in hours"),
    db: AsyncSession = Depends(get_db),
) -> list[FraudAlertResponse]:
    """Retrieve fraud alerts with optional filtering.

    Args:
        status: Optional alert status filter (NEEDS_REVIEW, INVESTIGATED, etc.).
        min_risk: Minimum risk score threshold (0-100).
        hours: Number of hours to look back from now.
        db: Async database session dependency.

    Returns:
        List of fraud alerts matching the filter criteria, ordered by most recent.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    stmt = (
        select(FraudAlert)
        .options(selectinload(FraudAlert.transaction))
        .where(FraudAlert.risk_score >= min_risk)
        .where(FraudAlert.created_at >= cutoff)
    )

    if status is not None:
        if status not in VALID_STATUSES:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status '{status}'. Must be one of: {', '.join(sorted(VALID_STATUSES))}",
            )
        stmt = stmt.where(FraudAlert.alert_status == status)

    stmt = stmt.order_by(FraudAlert.created_at.desc()).limit(200)

    result = await db.execute(stmt)
    alerts = result.scalars().all()

    return [FraudAlertResponse.model_validate(alert) for alert in alerts]


@alerts_router.get("/{alert_id}", response_model=FraudAlertResponse)
async def get_alert(
    alert_id: str,
    db: AsyncSession = Depends(get_db),
) -> FraudAlertResponse:
    """Retrieve a single fraud alert by its ID.

    Args:
        alert_id: Unique identifier of the alert.
        db: Async database session dependency.

    Returns:
        The requested fraud alert with its associated transaction.

    Raises:
        HTTPException: 404 if alert is not found.
    """
    stmt = (
        select(FraudAlert)
        .options(selectinload(FraudAlert.transaction))
        .where(FraudAlert.alert_id == alert_id)
    )

    result = await db.execute(stmt)
    alert = result.scalar_one_or_none()

    if alert is None:
        raise HTTPException(status_code=404, detail=f"Alert '{alert_id}' not found")

    return FraudAlertResponse.model_validate(alert)


@alerts_router.patch("/{alert_id}", response_model=FraudAlertResponse)
async def update_alert_status(
    alert_id: str,
    body: AlertStatusUpdate,
    db: AsyncSession = Depends(get_db),
) -> FraudAlertResponse:
    """Update the status of a fraud alert.

    Args:
        alert_id: Unique identifier of the alert to update.
        body: Request body containing the new alert status.
        db: Async database session dependency.

    Returns:
        The updated fraud alert.

    Raises:
        HTTPException: 400 if status value is invalid, 404 if alert not found.
    """
    if body.alert_status not in VALID_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid status '{body.alert_status}'. Must be one of: {', '.join(sorted(VALID_STATUSES))}",
        )

    stmt = (
        select(FraudAlert)
        .options(selectinload(FraudAlert.transaction))
        .where(FraudAlert.alert_id == alert_id)
    )

    result = await db.execute(stmt)
    alert = result.scalar_one_or_none()

    if alert is None:
        raise HTTPException(status_code=404, detail=f"Alert '{alert_id}' not found")

    alert.alert_status = body.alert_status
    alert.updated_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(alert, attribute_names=["transaction"])

    return FraudAlertResponse.model_validate(alert)
