"""Application configuration settings.

All values can be overridden via environment variables or a `.env` file in the
project root.  The ``pydantic-settings`` library handles parsing, type coercion,
and validation automatically.

Example `.env` override::

    RISK_SCORE_THRESHOLD=80
    HIGH_VALUE_THRESHOLD=500.0
    DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/fraud_db
"""

from __future__ import annotations

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Central configuration object for the SkyMart Fraud Detection System.

    Attributes:
        DATABASE_URL: SQLAlchemy async database connection string.
            Defaults to a local SQLite file via the aiosqlite driver.
        RISK_SCORE_THRESHOLD: Minimum composite risk score that causes a
            ``FraudAlert`` row to be created.  Transactions scoring below
            this threshold are stored but do not generate alerts.
        VELOCITY_WINDOW_MINUTES: Lookback window in minutes used by the
            velocity rule to count recent transactions for the same email.
        DECLINE_WINDOW_HOURS: Lookback window in hours used by the decline
            rate rule to count soft/hard declines for the same email.
        VELOCITY_MAX_TRANSACTIONS: Maximum number of transactions permitted
            within ``VELOCITY_WINDOW_MINUTES`` before the velocity rule
            triggers.  Transactions *above* this count trigger the rule.
        HIGH_VALUE_THRESHOLD: Amount in USD above which the high-value rule
            triggers on a single transaction.
        UNUSUAL_QUANTITY_THRESHOLD: Item quantity above which the unusual
            quantity rule triggers on a single transaction line item.
        APP_TITLE: Human-readable application name surfaced in the OpenAPI
            documentation and dashboard header.
        APP_VERSION: Semantic version string exposed in the OpenAPI spec.
    """

    DATABASE_URL: str = "sqlite+aiosqlite:///./fraud_detection.db"
    RISK_SCORE_THRESHOLD: int = 20  # alerts created for scores >= this
    VELOCITY_WINDOW_MINUTES: int = 10
    DECLINE_WINDOW_HOURS: int = 1
    VELOCITY_MAX_TRANSACTIONS: int = 3  # >3 triggers rule
    HIGH_VALUE_THRESHOLD: float = 1000.0
    UNUSUAL_QUANTITY_THRESHOLD: int = 5  # >5 triggers rule
    APP_TITLE: str = "SkyMart Fraud Detection System"
    APP_VERSION: str = "1.0.0"

    class Config:
        """Pydantic settings inner configuration."""

        env_file = ".env"


settings = Settings()
