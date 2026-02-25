"""Risk scoring module for the SkyMart fraud detection pipeline.

Aggregates individual rule results into a single composite risk score,
determines whether the transaction should be flagged, and provides a
per-rule breakdown for audit and dashboard display.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from src.config import settings
from src.pipeline.rules_engine import RuleResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ScoreResult:
    """Composite risk assessment for a single transaction.

    Attributes:
        risk_score: Cumulative score capped at 100.
        triggered_rules: Names of rules that fired.
        is_flagged: ``True`` when ``risk_score >= RISK_SCORE_THRESHOLD``.
        breakdown: Mapping of rule name to its individual score delta.
    """

    risk_score: int
    triggered_rules: list[str] = field(default_factory=list)
    is_flagged: bool = False
    breakdown: dict[str, int] = field(default_factory=dict)


class RiskScorer:
    """Calculates composite risk scores from a collection of rule results.

    The scorer sums the ``score_delta`` from every triggered rule, caps the
    total at 100, and compares the result against the configured threshold
    to determine whether the transaction should be flagged.

    Usage::

        scorer = RiskScorer()
        result = scorer.calculate(rule_results)
        if result.is_flagged:
            create_alert(...)
    """

    def calculate(self, rule_results: list[RuleResult]) -> ScoreResult:
        """Produce a composite risk score from individual rule evaluations.

        Args:
            rule_results: Results from ``RulesEngine.evaluate_all``.

        Returns:
            A ``ScoreResult`` containing the capped score, triggered rule
            names, flag status, and per-rule breakdown.
        """
        triggered_rules: list[str] = []
        breakdown: dict[str, int] = {}
        raw_score: int = 0

        for result in rule_results:
            if result.triggered:
                triggered_rules.append(result.rule_name)
                breakdown[result.rule_name] = result.score_delta
                raw_score += result.score_delta

        capped_score = min(raw_score, 100)
        is_flagged = capped_score >= settings.RISK_SCORE_THRESHOLD

        logger.info(
            "Risk score: %d (raw=%d, capped=%d, flagged=%s, rules=%s)",
            capped_score,
            raw_score,
            capped_score,
            is_flagged,
            triggered_rules,
        )

        return ScoreResult(
            risk_score=capped_score,
            triggered_rules=triggered_rules,
            is_flagged=is_flagged,
            breakdown=breakdown,
        )
