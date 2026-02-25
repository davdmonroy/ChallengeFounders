"""Transaction ingestion pipeline for SkyMart fraud detection.

This module provides the ``FraudDetectionPipeline`` class that orchestrates
the full lifecycle of a transaction: persistence, rule evaluation, risk
scoring, alert generation, and optional real-time broadcast via WebSocket.

Supported ingestion sources:
    - JSON files on disk (``ingest_from_json``)
    - In-memory transaction lists (``ingest_from_list``)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Callable, Coroutine
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.database import FraudAlert, Transaction, async_session
from src.pipeline.risk_scorer import RiskScorer, ScoreResult
from src.pipeline.rules_engine import RulesEngine

logger = logging.getLogger(__name__)

# Type alias for the optional WebSocket broadcast callback.
BroadcastCallback = Callable[[dict[str, Any]], Coroutine[Any, Any, None]]


class FraudDetectionPipeline:
    """End-to-end fraud detection pipeline.

    Accepts raw transaction dictionaries, persists them, evaluates fraud
    rules, calculates a composite risk score, and optionally creates a
    ``FraudAlert`` when the score exceeds the configured threshold.

    Args:
        broadcast_callback: Optional async callable invoked with alert data
            when a fraud alert is created.  Designed for WebSocket push to
            the real-time dashboard.

    Attributes:
        processed_count: Running total of successfully processed transactions.
        flagged_count: Running total of transactions that generated alerts.
    """

    def __init__(
        self,
        broadcast_callback: BroadcastCallback | None = None,
    ) -> None:
        self.broadcast_callback = broadcast_callback
        self.rules_engine = RulesEngine()
        self.scorer = RiskScorer()
        self.processed_count: int = 0
        self.flagged_count: int = 0

    async def process_transaction(
        self,
        tx_data: dict[str, Any],
        session: AsyncSession,
    ) -> ScoreResult | None:
        """Process a single transaction through the fraud detection pipeline.

        Steps:
            1. Deduplicate -- skip if ``transaction_id`` already exists.
            2. Persist the transaction.
            3. Evaluate all fraud rules.
            4. Calculate the composite risk score.
            5. If flagged, persist a ``FraudAlert`` and broadcast.

        Args:
            tx_data: Raw transaction dictionary (field names matching the
                ``Transaction`` ORM model).
            session: Active async database session.

        Returns:
            A ``ScoreResult`` on success, or ``None`` when the transaction
            was skipped as a duplicate.
        """
        transaction_id: str = tx_data.get("transaction_id", "")

        # --- 1. Duplicate check -------------------------------------------
        existing_stmt = select(Transaction).where(
            Transaction.transaction_id == transaction_id,
        )
        existing = await session.scalar(existing_stmt)
        if existing is not None:
            logger.warning(
                "Duplicate transaction %s -- skipping", transaction_id,
            )
            return None

        # --- 2. Persist transaction ----------------------------------------
        # Normalise the timestamp if it arrives as a string.
        raw_ts = tx_data.get("timestamp")
        if isinstance(raw_ts, str):
            parsed = datetime.fromisoformat(raw_ts)
            # Ensure timezone-aware (UTC) so rule engine comparisons don't fail
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            tx_data["timestamp"] = parsed

        transaction = Transaction(**tx_data)
        session.add(transaction)
        await session.flush()  # Ensure the row is visible to rule queries.

        # --- 3. Evaluate rules ---------------------------------------------
        rule_results = await self.rules_engine.evaluate_all(transaction, session)

        # --- 4. Calculate risk score ---------------------------------------
        score_result = self.scorer.calculate(rule_results)

        # --- 5. Flag and alert if necessary --------------------------------
        if score_result.is_flagged:
            alert = FraudAlert(
                alert_id=str(uuid4()),
                transaction_id=transaction_id,
                risk_score=score_result.risk_score,
                triggered_rules=score_result.triggered_rules,
                alert_status="NEW",
                created_at=datetime.now(timezone.utc),
            )
            session.add(alert)
            self.flagged_count += 1

            logger.warning(
                "FRAUD ALERT for %s -- score %d, rules %s",
                transaction_id,
                score_result.risk_score,
                score_result.triggered_rules,
            )

            if self.broadcast_callback is not None:
                alert_data: dict[str, Any] = {
                    "alert_id": alert.alert_id,
                    "transaction_id": transaction_id,
                    "risk_score": score_result.risk_score,
                    "triggered_rules": score_result.triggered_rules,
                    "alert_status": "NEW",
                    "amount_usd": transaction.amount_usd,
                    "customer_email": transaction.customer_email,
                    "product_category": transaction.product_category,
                }
                await self.broadcast_callback(alert_data)

        await session.commit()
        self.processed_count += 1
        return score_result

    async def ingest_from_json(
        self,
        file_path: str,
        delay_seconds: float = 0.0,
    ) -> dict[str, Any]:
        """Ingest transactions from a JSON file.

        The file must contain a JSON array of transaction objects at the
        top level.

        Args:
            file_path: Path to the JSON file.
            delay_seconds: Artificial delay between transactions to simulate
                real-time arrival.

        Returns:
            A summary dictionary with keys ``total``, ``flagged``, and
            ``processing_time_seconds``.
        """
        path = Path(file_path)
        logger.info("Loading transactions from %s", path.resolve())

        with path.open("r", encoding="utf-8") as fh:
            transactions: list[dict[str, Any]] = json.load(fh)

        return await self._ingest(transactions, delay_seconds)

    async def ingest_from_list(
        self,
        transactions: list[dict[str, Any]],
        delay_seconds: float = 0.0,
    ) -> dict[str, Any]:
        """Ingest transactions from an in-memory list.

        Args:
            transactions: List of transaction dictionaries.
            delay_seconds: Artificial delay between transactions.

        Returns:
            A summary dictionary identical to ``ingest_from_json``.
        """
        return await self._ingest(transactions, delay_seconds)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _ingest(
        self,
        transactions: list[dict[str, Any]],
        delay_seconds: float,
    ) -> dict[str, Any]:
        """Shared ingestion loop used by both public entry points.

        Args:
            transactions: Ordered list of raw transaction dicts.
            delay_seconds: Inter-transaction delay in seconds.

        Returns:
            Pipeline run summary.
        """
        total = len(transactions)
        logger.info("Starting ingestion of %d transactions", total)
        start_time = time.perf_counter()

        for idx, tx_data in enumerate(transactions, start=1):
            tx_id = tx_data.get("transaction_id", "UNKNOWN")
            async with async_session() as session:
                score_result = await self.process_transaction(tx_data, session)

            if score_result is not None:
                triggered = score_result.triggered_rules
                print(
                    f"Processing [{idx}/{total}] {tx_id} | "
                    f"Score: {score_result.risk_score} | "
                    f"Rules: {triggered}"
                )
            else:
                print(f"Processing [{idx}/{total}] {tx_id} | SKIPPED (duplicate)")

            if delay_seconds > 0:
                await asyncio.sleep(delay_seconds)

        elapsed = time.perf_counter() - start_time
        summary: dict[str, Any] = {
            "total": self.processed_count,
            "flagged": self.flagged_count,
            "processing_time_seconds": round(elapsed, 4),
        }
        logger.info("Ingestion complete: %s", summary)
        return summary
