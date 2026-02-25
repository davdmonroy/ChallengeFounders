"""SQLAlchemy 2.0 async database layer.

This module provides:

- ``Base``          — declarative base class shared by all ORM models.
- ``Transaction``   — ORM model representing an ingested e-commerce transaction.
- ``FraudAlert``    — ORM model representing a fraud alert raised for a transaction.
- ``engine``        — shared ``AsyncEngine`` instance.
- ``async_session`` — ``async_sessionmaker`` factory bound to ``engine``.
- ``get_db``        — async generator for use with FastAPI ``Depends``.
- ``create_tables`` — coroutine that issues ``CREATE TABLE IF NOT EXISTS`` for all models.

SQLite is configured to run in WAL (Write-Ahead Logging) mode so that concurrent
read queries from the API are never blocked by an ongoing ingestion write.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    event,
    func,
    text,
)
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from src.config import settings

# ---------------------------------------------------------------------------
# Engine & session factory
# ---------------------------------------------------------------------------


def _set_sqlite_wal(dbapi_connection: Any, connection_record: Any) -> None:  # noqa: ANN401
    """Enable WAL mode immediately after each new SQLite connection is created.

    WAL mode allows concurrent readers alongside a single writer, which is
    critical for the dashboard querying the database while the ingestion
    pipeline is writing new transaction rows.

    Args:
        dbapi_connection: The raw DBAPI connection handed to the listener by
            SQLAlchemy's ``connect`` event.
        connection_record: Internal SQLAlchemy connection pool record
            (not used here but required by the event signature).
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL;")
    cursor.close()


engine: AsyncEngine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    future=True,
)

# Register WAL mode only when the backend is SQLite.
if "sqlite" in settings.DATABASE_URL:
    event.listen(engine.sync_engine, "connect", _set_sqlite_wal)


async_session: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)

# ---------------------------------------------------------------------------
# Declarative base
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models.

    All subclasses inherit from this base so that ``create_tables`` can
    iterate ``Base.metadata`` to issue ``CREATE TABLE`` statements.
    """


# ---------------------------------------------------------------------------
# ORM Models
# ---------------------------------------------------------------------------


class Transaction(Base):
    """ORM model for the ``transactions`` table.

    Each row represents a single e-commerce transaction ingested from the
    CSV feed or via the POST /transactions REST endpoint.

    Indexed columns:
        - ``customer_email`` — used heavily by velocity and decline-rate rules.
        - ``customer_ip``    — used for IP-based suspicious activity lookups.
        - ``card_bin``       — used for BIN-level fraud pattern detection.
    """

    __tablename__ = "transactions"

    __table_args__ = (
        Index("ix_transactions_customer_email", "customer_email"),
        Index("ix_transactions_customer_ip", "customer_ip"),
        Index("ix_transactions_card_bin", "card_bin"),
    )

    transaction_id: Mapped[str] = mapped_column(
        String,
        primary_key=True,
        doc="UUID string sourced from the upstream transaction system.",
    )
    timestamp: Mapped[DateTime] = mapped_column(
        DateTime,
        nullable=False,
        doc="UTC timestamp of when the transaction event occurred.",
    )
    customer_email: Mapped[str] = mapped_column(
        String,
        nullable=False,
        doc="Email address of the customer who initiated the transaction.",
    )
    customer_ip: Mapped[str] = mapped_column(
        String,
        nullable=False,
        doc="IP address of the client device at transaction time.",
    )
    billing_country: Mapped[str] = mapped_column(
        String,
        nullable=False,
        doc="ISO 3166-1 alpha-2 country code of the billing address.",
    )
    shipping_country: Mapped[str] = mapped_column(
        String,
        nullable=False,
        doc="ISO 3166-1 alpha-2 country code of the shipping destination.",
    )
    card_bin: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
        doc="First six digits of the payment card (BIN). Null for non-card methods.",
    )
    payment_method: Mapped[str] = mapped_column(
        String,
        nullable=False,
        doc="Payment instrument used. One of: CREDIT_CARD, GOPAY, OVO, BANK_TRANSFER.",
    )
    amount_usd: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        doc="Total transaction amount in United States Dollars.",
    )
    status: Mapped[str] = mapped_column(
        String,
        nullable=False,
        doc="Transaction outcome. One of: APPROVED, SOFT_DECLINED, HARD_DECLINED.",
    )
    product_category: Mapped[str] = mapped_column(
        String,
        nullable=False,
        doc="Top-level product category. One of: LAPTOP, SMARTPHONE, CAMERA, ACCESSORIES.",
    )
    quantity: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        doc="Number of units purchased in this transaction.",
    )
    unit_price: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        doc="Price per unit in USD at the time of purchase.",
    )
    device_fingerprint: Mapped[str | None] = mapped_column(
        String,
        nullable=True,
        doc="Hash representing the client browser or device configuration.",
    )
    is_first_purchase: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        doc="True when this is the customer's first-ever purchase on the platform.",
    )
    created_at: Mapped[DateTime] = mapped_column(
        DateTime,
        server_default=func.now(),
        doc="Database row insertion timestamp (server-side default).",
    )

    # Relationship — one transaction may have zero or one fraud alert.
    fraud_alert: Mapped[FraudAlert | None] = relationship(
        "FraudAlert",
        back_populates="transaction",
        uselist=False,
        lazy="select",
    )


class FraudAlert(Base):
    """ORM model for the ``fraud_alerts`` table.

    A ``FraudAlert`` is created whenever a transaction's composite risk score
    meets or exceeds the ``RISK_SCORE_THRESHOLD`` setting.  Analysts use the
    ``alert_status`` field to track the review workflow.

    Valid ``alert_status`` values:
        - ``NEEDS_REVIEW``     — newly created, awaiting analyst attention.
        - ``INVESTIGATED``     — analyst has started reviewing.
        - ``CLEARED``          — analyst confirmed the transaction is legitimate.
        - ``CONFIRMED_FRAUD``  — analyst confirmed fraudulent activity.
    """

    __tablename__ = "fraud_alerts"

    alert_id: Mapped[str] = mapped_column(
        String,
        primary_key=True,
        doc="UUID string generated at alert creation time.",
    )
    transaction_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("transactions.transaction_id"),
        nullable=False,
        doc="Foreign key reference to the transaction that triggered this alert.",
    )
    risk_score: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        doc="Composite risk score in the range [0, 100] computed by the scorer.",
    )
    triggered_rules: Mapped[Any] = mapped_column(
        # SQLAlchemy's JSON type stores Python lists/dicts as JSON strings in
        # SQLite and as native JSONB in PostgreSQL.
        type_=__import__("sqlalchemy").JSON,
        nullable=False,
        doc="Ordered list of rule label strings that contributed to the risk score.",
    )
    alert_status: Mapped[str] = mapped_column(
        String,
        nullable=False,
        default="NEEDS_REVIEW",
        doc="Current review workflow state for this alert.",
    )
    created_at: Mapped[DateTime] = mapped_column(
        DateTime,
        server_default=func.now(),
        doc="Database row insertion timestamp (server-side default).",
    )
    updated_at: Mapped[DateTime | None] = mapped_column(
        DateTime,
        onupdate=func.now(),
        nullable=True,
        doc="Timestamp of the most recent status update, populated on first PATCH.",
    )

    # Relationship — back reference to the parent transaction.
    transaction: Mapped[Transaction] = relationship(
        "Transaction",
        back_populates="fraud_alert",
        lazy="select",
    )


# ---------------------------------------------------------------------------
# Database lifecycle helpers
# ---------------------------------------------------------------------------


async def create_tables() -> None:
    """Create all ORM-mapped tables if they do not already exist.

    Uses ``Base.metadata.create_all`` via the async engine's ``run_sync``
    helper.  Safe to call on every application startup because it is a
    no-op when tables already exist.

    Example::

        from src.models.database import create_tables
        import asyncio
        asyncio.run(create_tables())
    """
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Async generator that yields a database session for each request.

    Designed for use with FastAPI's ``Depends`` dependency injection system.
    The session is automatically closed (and any pending transaction rolled
    back) when the request context exits, whether normally or via an exception.

    Yields:
        AsyncSession: A live SQLAlchemy async session bound to ``engine``.

    Example::

        from fastapi import Depends
        from src.models.database import get_db

        @app.get("/example")
        async def example(db: AsyncSession = Depends(get_db)):
            result = await db.execute(select(Transaction))
            return result.scalars().all()
    """
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.close()
