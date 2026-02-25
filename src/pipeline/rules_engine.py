"""Fraud detection rules engine for SkyMart Indonesia.

This module implements a rule-based fraud detection system that evaluates
incoming transactions against five configurable rules. Each rule produces
a score delta that feeds into the downstream risk scorer.

Rules:
    VELOCITY              -- Excessive transaction frequency from the same email.
    HIGH_VALUE_FIRST_PURCHASE -- Large first-time purchases.
    MULTIPLE_DECLINES     -- Approved transaction preceded by recent declines.
    GEOGRAPHIC_MISMATCH   -- Billing/shipping country divergence.
    UNUSUAL_QUANTITY      -- Bulk orders for high-value electronics.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import settings
from src.models.database import Transaction

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RuleResult:
    """Immutable result produced by a single fraud detection rule.

    Attributes:
        rule_name: Canonical identifier for the rule (e.g. ``"VELOCITY"``).
        triggered: Whether the rule condition was satisfied.
        score_delta: Points to add to the cumulative risk score when triggered.
        reason: Human-readable explanation of why the rule did or did not fire.
    """

    rule_name: str
    triggered: bool
    score_delta: int
    reason: str


class RulesEngine:
    """Orchestrates evaluation of all fraud detection rules against a transaction.

    Usage::

        engine = RulesEngine()
        results = await engine.evaluate_all(transaction, session)
    """

    async def evaluate_velocity(
        self,
        tx: Transaction,
        session: AsyncSession,
    ) -> RuleResult:
        """Evaluate the VELOCITY rule.

        Counts transactions from the same ``customer_email`` within the
        configured velocity window.  If the count exceeds the threshold the
        rule fires.

        Args:
            tx: The transaction being evaluated.
            session: Active async database session.

        Returns:
            A ``RuleResult`` indicating whether velocity was exceeded.
        """
        # Use the transaction's own timestamp as reference (supports historical data replay)
        ref_time = tx.timestamp if tx.timestamp.tzinfo else tx.timestamp.replace(tzinfo=timezone.utc)
        cutoff = ref_time - timedelta(
            minutes=settings.VELOCITY_WINDOW_MINUTES,
        )

        stmt = (
            select(func.count())
            .select_from(Transaction)
            .where(
                Transaction.customer_email == tx.customer_email,
                Transaction.timestamp >= cutoff,
                Transaction.timestamp <= ref_time,
            )
        )
        count: int = await session.scalar(stmt) or 0

        triggered = count > settings.VELOCITY_MAX_TRANSACTIONS
        reason = (
            f"Found {count} transactions from {tx.customer_email} in the last "
            f"{settings.VELOCITY_WINDOW_MINUTES} minutes "
            f"(threshold: {settings.VELOCITY_MAX_TRANSACTIONS})"
        )

        logger.debug("VELOCITY rule: count=%d, triggered=%s", count, triggered)
        return RuleResult(
            rule_name="VELOCITY",
            triggered=triggered,
            score_delta=30 if triggered else 0,
            reason=reason,
        )

    async def evaluate_high_value_first(
        self,
        tx: Transaction,
        session: AsyncSession,
    ) -> RuleResult:
        """Evaluate the HIGH_VALUE_FIRST_PURCHASE rule.

        Flags first-time buyers whose transaction amount exceeds the
        configured high-value threshold.

        Args:
            tx: The transaction being evaluated.
            session: Active async database session (unused, kept for
                interface consistency).

        Returns:
            A ``RuleResult`` for the high-value first-purchase check.
        """
        triggered = (
            tx.amount_usd > settings.HIGH_VALUE_THRESHOLD and tx.is_first_purchase
        )
        reason = (
            f"Amount ${tx.amount_usd:.2f} "
            f"{'exceeds' if tx.amount_usd > settings.HIGH_VALUE_THRESHOLD else 'within'} "
            f"threshold ${settings.HIGH_VALUE_THRESHOLD:.2f}, "
            f"first_purchase={tx.is_first_purchase}"
        )

        logger.debug("HIGH_VALUE_FIRST_PURCHASE rule: triggered=%s", triggered)
        return RuleResult(
            rule_name="HIGH_VALUE_FIRST_PURCHASE",
            triggered=triggered,
            score_delta=35 if triggered else 0,
            reason=reason,
        )

    async def evaluate_multiple_declines(
        self,
        tx: Transaction,
        session: AsyncSession,
    ) -> RuleResult:
        """Evaluate the MULTIPLE_DECLINES rule.

        Only applies to APPROVED transactions.  Checks whether the same
        ``customer_email`` had three or more declined transactions in the
        recent decline window.

        Args:
            tx: The transaction being evaluated.
            session: Active async database session.

        Returns:
            A ``RuleResult`` for the multiple-declines check.
        """
        if tx.status != "APPROVED":
            return RuleResult(
                rule_name="MULTIPLE_DECLINES",
                triggered=False,
                score_delta=0,
                reason=f"Transaction status is {tx.status}, rule only applies to APPROVED",
            )

        # Use the transaction's own timestamp as reference (supports historical data replay)
        ref_time = tx.timestamp if tx.timestamp.tzinfo else tx.timestamp.replace(tzinfo=timezone.utc)
        cutoff = ref_time - timedelta(
            hours=settings.DECLINE_WINDOW_HOURS,
        )

        stmt = (
            select(func.count())
            .select_from(Transaction)
            .where(
                Transaction.customer_email == tx.customer_email,
                Transaction.status.in_(["SOFT_DECLINED", "HARD_DECLINED"]),
                Transaction.timestamp >= cutoff,
                Transaction.timestamp < ref_time,
            )
        )
        declined_count: int = await session.scalar(stmt) or 0

        triggered = declined_count >= 3
        reason = (
            f"Found {declined_count} declined transactions from "
            f"{tx.customer_email} in the last {settings.DECLINE_WINDOW_HOURS} hour(s) "
            f"(threshold: 3)"
        )

        logger.debug(
            "MULTIPLE_DECLINES rule: declined_count=%d, triggered=%s",
            declined_count,
            triggered,
        )
        return RuleResult(
            rule_name="MULTIPLE_DECLINES",
            triggered=triggered,
            score_delta=25 if triggered else 0,
            reason=reason,
        )

    async def evaluate_geographic_mismatch(
        self,
        tx: Transaction,
        session: AsyncSession,
    ) -> RuleResult:
        """Evaluate the GEOGRAPHIC_MISMATCH rule.

        Flags transactions where the billing country differs from the
        shipping country.

        Args:
            tx: The transaction being evaluated.
            session: Active async database session (unused, kept for
                interface consistency).

        Returns:
            A ``RuleResult`` for the geographic mismatch check.
        """
        triggered = tx.billing_country != tx.shipping_country
        reason = (
            f"Billing country ({tx.billing_country}) "
            f"{'!=' if triggered else '=='} "
            f"shipping country ({tx.shipping_country})"
        )

        logger.debug("GEOGRAPHIC_MISMATCH rule: triggered=%s", triggered)
        return RuleResult(
            rule_name="GEOGRAPHIC_MISMATCH",
            triggered=triggered,
            score_delta=20 if triggered else 0,
            reason=reason,
        )

    async def evaluate_unusual_quantity(
        self,
        tx: Transaction,
        session: AsyncSession,
    ) -> RuleResult:
        """Evaluate the UNUSUAL_QUANTITY rule.

        Flags bulk orders of high-value electronics (LAPTOP, SMARTPHONE,
        CAMERA) that exceed the configured quantity threshold.

        Args:
            tx: The transaction being evaluated.
            session: Active async database session (unused, kept for
                interface consistency).

        Returns:
            A ``RuleResult`` for the unusual quantity check.
        """
        high_value_categories = {"LAPTOP", "SMARTPHONE", "CAMERA"}
        triggered = (
            tx.quantity > settings.UNUSUAL_QUANTITY_THRESHOLD
            and tx.product_category in high_value_categories
        )
        reason = (
            f"Quantity {tx.quantity} of {tx.product_category} "
            f"{'exceeds' if tx.quantity > settings.UNUSUAL_QUANTITY_THRESHOLD else 'within'} "
            f"threshold {settings.UNUSUAL_QUANTITY_THRESHOLD}"
            + (
                f", category {'in' if tx.product_category in high_value_categories else 'not in'} "
                f"high-value set"
            )
        )

        logger.debug("UNUSUAL_QUANTITY rule: triggered=%s", triggered)
        return RuleResult(
            rule_name="UNUSUAL_QUANTITY",
            triggered=triggered,
            score_delta=15 if triggered else 0,
            reason=reason,
        )

    async def evaluate_all(
        self,
        tx: Transaction,
        session: AsyncSession,
    ) -> list[RuleResult]:
        """Run every fraud detection rule against a single transaction.

        Rules are evaluated sequentially to maintain deterministic ordering
        and consistent database reads.

        Args:
            tx: The transaction to evaluate.
            session: Active async database session.

        Returns:
            A list of ``RuleResult`` objects, one per rule.
        """
        logger.info(
            "Evaluating all rules for transaction %s", tx.transaction_id,
        )
        results: list[RuleResult] = [
            await self.evaluate_velocity(tx, session),
            await self.evaluate_high_value_first(tx, session),
            await self.evaluate_multiple_declines(tx, session),
            await self.evaluate_geographic_mismatch(tx, session),
            await self.evaluate_unusual_quantity(tx, session),
        ]
        triggered_names = [r.rule_name for r in results if r.triggered]
        logger.info(
            "Transaction %s triggered %d rule(s): %s",
            tx.transaction_id,
            len(triggered_names),
            triggered_names,
        )
        return results
