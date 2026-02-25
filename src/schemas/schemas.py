"""Pydantic v2 schemas for request validation and response serialization.

This module defines all data transfer objects (DTOs) used across the
SkyMart Fraud Detection System:

- ``TransactionCreate``          — incoming transaction payload (POST body or CSV row).
- ``TransactionResponse``        — transaction data returned from the API.
- ``FraudAlertResponse``         — fraud alert data returned from the API.
- ``AlertStatusUpdate``          — request body for PATCH /alerts/{id}/status.
- ``MetricsResponse``            — aggregate metrics for the analyst dashboard.
- ``RelatedTransactionsResponse``— related transactions grouped by email / IP / BIN.

All models use ``from __future__ import annotations`` for deferred evaluation
of type hints, enabling forward references within the same module.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class TransactionCreate(BaseModel):
    """Schema for creating or ingesting a new transaction.

    Used both as the FastAPI request body for ``POST /transactions`` and
    as the validated intermediate object produced by the CSV ingestion
    pipeline before the record is written to the database.

    Attributes:
        transaction_id: Unique identifier sourced from the upstream system.
            Must be a non-empty string; typically a UUID v4.
        timestamp: UTC datetime of the transaction event.
        customer_email: Email address of the purchasing customer.
        customer_ip: IPv4 or IPv6 address of the client device.
        billing_country: ISO 3166-1 alpha-2 billing address country code.
        shipping_country: ISO 3166-1 alpha-2 shipping destination country code.
        card_bin: First six digits of the payment card.  ``None`` for
            non-card payment methods such as GOPAY or OVO.
        payment_method: Payment instrument.
            Allowed values: ``CREDIT_CARD``, ``GOPAY``, ``OVO``, ``BANK_TRANSFER``.
        amount_usd: Total transaction amount in USD.  Must be a positive number.
        status: Outcome of the transaction.
            Allowed values: ``APPROVED``, ``SOFT_DECLINED``, ``HARD_DECLINED``.
        product_category: Top-level product category.
            Allowed values: ``LAPTOP``, ``SMARTPHONE``, ``CAMERA``, ``ACCESSORIES``.
        quantity: Number of units purchased.  Defaults to 1.
        unit_price: Per-unit price in USD at transaction time.
        device_fingerprint: Optional hash representing the client browser or
            device configuration, used for device-level pattern detection.
        is_first_purchase: ``True`` when this is the customer's first-ever
            purchase on the platform.  Defaults to ``False``.
    """

    transaction_id: str = Field(
        ...,
        description="Unique transaction identifier (UUID).",
    )
    timestamp: datetime = Field(
        ...,
        description="UTC datetime of the transaction event.",
    )
    customer_email: str = Field(
        ...,
        description="Email address of the purchasing customer.",
    )
    customer_ip: str = Field(
        ...,
        description="IPv4 or IPv6 address of the client device.",
    )
    billing_country: str = Field(
        ...,
        description="ISO 3166-1 alpha-2 code of the billing country.",
    )
    shipping_country: str = Field(
        ...,
        description="ISO 3166-1 alpha-2 code of the shipping destination country.",
    )
    card_bin: str | None = Field(
        default=None,
        description="First six digits of the payment card.  Null for non-card methods.",
    )
    payment_method: str = Field(
        ...,
        description="Payment instrument: CREDIT_CARD, GOPAY, OVO, or BANK_TRANSFER.",
    )
    amount_usd: float = Field(
        ...,
        description="Total transaction amount in United States Dollars.",
    )
    status: str = Field(
        ...,
        description="Transaction outcome: APPROVED, SOFT_DECLINED, or HARD_DECLINED.",
    )
    product_category: str = Field(
        ...,
        description="Product category: LAPTOP, SMARTPHONE, CAMERA, or ACCESSORIES.",
    )
    quantity: int = Field(
        default=1,
        description="Number of units purchased.  Defaults to 1.",
    )
    unit_price: float = Field(
        ...,
        description="Price per unit in USD at the time of purchase.",
    )
    device_fingerprint: str | None = Field(
        default=None,
        description="Optional hash of the client browser or device configuration.",
    )
    is_first_purchase: bool = Field(
        default=False,
        description="True when this is the customer's first-ever purchase.",
    )


class TransactionResponse(TransactionCreate):
    """Schema for a transaction as returned by the API.

    Extends ``TransactionCreate`` with the server-generated ``created_at``
    timestamp that is populated by the database on row insertion.

    ``from_attributes=True`` enables direct construction from a
    ``Transaction`` ORM instance without manual field mapping.

    Attributes:
        created_at: Server-side timestamp of when the row was inserted into
            the ``transactions`` table.
    """

    created_at: datetime = Field(
        ...,
        description="Server-side row insertion timestamp.",
    )

    model_config = ConfigDict(from_attributes=True)


class FraudAlertResponse(BaseModel):
    """Schema for a fraud alert as returned by the API.

    Includes the alert's composite risk score, the list of rule labels that
    contributed to the score, the current review status, and optionally the
    full nested transaction object.

    ``from_attributes=True`` enables direct construction from a
    ``FraudAlert`` ORM instance with its eagerly-loaded ``transaction``
    relationship.

    Attributes:
        alert_id: Unique identifier of the fraud alert (UUID).
        transaction_id: Foreign key reference to the source transaction.
        risk_score: Composite risk score in the range [0, 100].
        triggered_rules: Ordered list of rule label strings that fired.
            Example: ``["VELOCITY", "GEO_MISMATCH"]``.
        alert_status: Current review workflow state.
            One of: ``NEEDS_REVIEW``, ``INVESTIGATED``, ``CLEARED``,
            ``CONFIRMED_FRAUD``.
        created_at: Timestamp of alert creation.
        updated_at: Timestamp of the most recent status update.
            ``None`` until the first PATCH request is processed.
        transaction: Full transaction detail, included when the alert is
            fetched with ``GET /alerts/{alert_id}``.  ``None`` in list views.
    """

    alert_id: str = Field(
        ...,
        description="Unique fraud alert identifier (UUID).",
    )
    transaction_id: str = Field(
        ...,
        description="Foreign key referencing the source transaction.",
    )
    risk_score: int = Field(
        ...,
        description="Composite risk score in the range [0, 100].",
    )
    triggered_rules: list[str] = Field(
        ...,
        description="Rule labels that contributed to the risk score.",
    )
    alert_status: str = Field(
        ...,
        description=(
            "Review workflow state: NEEDS_REVIEW, INVESTIGATED, "
            "CLEARED, or CONFIRMED_FRAUD."
        ),
    )
    created_at: datetime = Field(
        ...,
        description="Timestamp of alert creation.",
    )
    updated_at: datetime | None = Field(
        default=None,
        description="Timestamp of the most recent status update.  Null until first PATCH.",
    )
    transaction: TransactionResponse | None = Field(
        default=None,
        description="Full transaction detail.  Populated in single-alert responses.",
    )

    model_config = ConfigDict(from_attributes=True)


class AlertStatusUpdate(BaseModel):
    """Request body schema for ``PATCH /alerts/{alert_id}/status``.

    Allows an analyst to advance the review workflow state of a fraud alert.

    Attributes:
        alert_status: New status to assign to the alert.
            Must be one of: ``NEEDS_REVIEW``, ``INVESTIGATED``,
            ``CLEARED``, ``CONFIRMED_FRAUD``.
    """

    alert_status: str = Field(
        ...,
        description=(
            "New alert status.  One of: NEEDS_REVIEW, INVESTIGATED, "
            "CLEARED, CONFIRMED_FRAUD."
        ),
    )


class MetricsResponse(BaseModel):
    """Aggregate metrics for the analyst dashboard.

    All fields are lists of dicts so that the dashboard can pass them
    directly to charting libraries without transformation.

    Attributes:
        hourly_alert_volume: Alert counts bucketed by hour.
            Each element: ``{"hour": "2024-01-01T14:00", "count": 5}``.
        risk_score_distribution: Alert counts bucketed by 10-point score bands.
            Each element: ``{"bucket": "70-79", "count": 12}``.
        top_triggered_rules: Alert counts grouped by rule label, descending.
            Each element: ``{"rule": "VELOCITY", "count": 25}``.
        top_suspicious_emails: Customers with the most alerts, descending.
            Each element: ``{"email": "suspect@example.com", "alert_count": 3}``.
        top_suspicious_ips: IP addresses with the most alerts, descending.
            Each element: ``{"ip": "1.2.3.4", "alert_count": 2}``.
        top_suspicious_bins: Card BINs with the most alerts, descending.
            Each element: ``{"bin": "411111", "alert_count": 4}``.
        total_alerts_24h: Total number of alerts created in the last 24 hours.
        high_risk_alerts: Number of alerts with ``risk_score >= 80``.
    """

    hourly_alert_volume: list[dict] = Field(
        ...,
        description=(
            'Alert counts per hour. Each dict: {"hour": "2024-01-01T14:00", "count": 5}.'
        ),
    )
    risk_score_distribution: list[dict] = Field(
        ...,
        description=(
            'Alert counts per 10-point score band. '
            'Each dict: {"bucket": "70-79", "count": 12}.'
        ),
    )
    top_triggered_rules: list[dict] = Field(
        ...,
        description=(
            'Alert counts per rule label, descending. '
            'Each dict: {"rule": "VELOCITY", "count": 25}.'
        ),
    )
    top_suspicious_emails: list[dict] = Field(
        ...,
        description=(
            'Customers ranked by alert count, descending. '
            'Each dict: {"email": "x@y.com", "alert_count": 3}.'
        ),
    )
    top_suspicious_ips: list[dict] = Field(
        ...,
        description=(
            'IP addresses ranked by alert count, descending. '
            'Each dict: {"ip": "1.2.3.4", "alert_count": 2}.'
        ),
    )
    top_suspicious_bins: list[dict] = Field(
        ...,
        description=(
            'Card BINs ranked by alert count, descending. '
            'Each dict: {"bin": "411111", "alert_count": 4}.'
        ),
    )
    total_alerts_24h: int = Field(
        ...,
        description="Total number of alerts created in the last 24 hours.",
    )
    high_risk_alerts: int = Field(
        ...,
        description="Number of alerts with risk_score >= 80.",
    )


class RelatedTransactionsResponse(BaseModel):
    """Grouped related transactions for a given transaction.

    Returned by ``GET /transactions/{transaction_id}/related``.  Provides the
    analyst with all historical context needed to assess whether a flagged
    transaction is part of a larger fraud pattern.

    Attributes:
        transaction: The primary transaction being investigated.
        related_by_email: Other transactions sharing the same
            ``customer_email``, ordered by timestamp descending.
        related_by_ip: Other transactions sharing the same
            ``customer_ip``, ordered by timestamp descending.
        related_by_bin: Other transactions sharing the same
            ``card_bin``, ordered by timestamp descending.
            Empty list when the primary transaction has no ``card_bin``.
    """

    transaction: TransactionResponse = Field(
        ...,
        description="The primary transaction being investigated.",
    )
    related_by_email: list[TransactionResponse] = Field(
        ...,
        description=(
            "Other transactions sharing the same customer_email, "
            "newest first."
        ),
    )
    related_by_ip: list[TransactionResponse] = Field(
        ...,
        description=(
            "Other transactions sharing the same customer_ip, "
            "newest first."
        ),
    )
    related_by_bin: list[TransactionResponse] = Field(
        ...,
        description=(
            "Other transactions sharing the same card_bin, newest first.  "
            "Empty when the primary transaction has no card_bin."
        ),
    )
