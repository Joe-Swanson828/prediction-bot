"""
engine/agent.py — Agentic self-improvement system.

IMPORTANT: This is NOT an LLM-based agent. It is a purely deterministic,
rule-based performance monitoring and weight adjustment system.

The agent:
  1. Tracks rolling win rates per signal type per market category
  2. Every N closed trades, evaluates whether each signal type has been
     contributing positively or negatively to outcomes
  3. Adjusts signal weights accordingly (bounded adjustments)
  4. Logs every adjustment with full reasoning to the agent_log table

Rules:
  - Evaluate after every 20 closed trades per category
  - Signal accuracy > 65% → increase weight by ADJUSTMENT_STEP (0.05)
  - Signal accuracy < 40% → decrease weight by ADJUSTMENT_STEP (0.05)
  - After adjustment, renormalize all three weights to sum to 1.0
  - Minimum weight per signal: 0.05 (never fully ignore a signal type)
  - Maximum weight per signal: 0.70 (never over-rely on one type)
  - All adjustments are logged and visible in the Agent Insights dashboard tab

Usage:
    agent = AgentEngine()
    await agent.evaluate_and_adjust("crypto")   # call periodically
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from database.connection import execute_query, execute_write
from database.schema import get_current_weights


class AgentEngine:
    """
    Rule-based performance analysis and strategy weight adjustment.
    Operates on closed trade history stored in the database.
    """

    # How many closed trades per category to evaluate before adjusting
    EVALUATION_PERIOD: int = 20

    # Weight adjustment step per evaluation cycle
    ADJUSTMENT_STEP: float = 0.05

    # Accuracy thresholds
    HIGH_ACCURACY_THRESHOLD: float = 0.65   # > 65% → increase weight
    LOW_ACCURACY_THRESHOLD: float = 0.40    # < 40% → decrease weight

    # Weight bounds
    MIN_WEIGHT: float = 0.05
    MAX_WEIGHT: float = 0.70

    # Market categories this bot operates in
    CATEGORIES: list = ["sports", "crypto", "weather"]

    def __init__(self) -> None:
        # Track how many trades have been processed per category
        # so we know when to trigger the next evaluation
        self._last_evaluated_count: dict = {cat: 0 for cat in self.CATEGORIES}

    async def maybe_evaluate(self) -> None:
        """
        Check all categories and run evaluation if enough new trades have closed.
        Call this periodically from the main engine loop.
        """
        for category in self.CATEGORIES:
            current_count = self._get_closed_trade_count(category)
            last_count = self._last_evaluated_count.get(category, 0)
            new_trades = current_count - last_count

            if new_trades >= self.EVALUATION_PERIOD:
                await self.evaluate_and_adjust(category)
                self._last_evaluated_count[category] = current_count

    async def evaluate_and_adjust(self, category: str) -> Optional[dict]:
        """
        Evaluate signal performance for a category and adjust weights if warranted.

        Args:
            category: 'sports' | 'crypto' | 'weather'

        Returns:
            Adjustment dict if weights were changed, None if no change needed.
        """
        # Get recent closed trades for this category
        trades = self._get_recent_closed_trades(category, self.EVALUATION_PERIOD)
        if len(trades) < self.EVALUATION_PERIOD:
            return None  # not enough data yet

        # Compute accuracy per signal type
        ta_accuracy = self._compute_signal_accuracy(trades, "ta_score")
        sentiment_accuracy = self._compute_signal_accuracy(trades, "sentiment_score")
        speed_accuracy = self._compute_signal_accuracy(trades, "speed_score")

        accuracies = {
            "ta": ta_accuracy,
            "sentiment": sentiment_accuracy,
            "speed": speed_accuracy,
        }

        # Get current weights
        current_weights = get_current_weights(category)
        new_weights = dict(current_weights)

        adjustments_made = []

        for signal_name, accuracy in accuracies.items():
            weight_key = signal_name
            current_weight = current_weights.get(signal_name, 0.33)

            if accuracy > self.HIGH_ACCURACY_THRESHOLD:
                # Signal performing well — increase its weight
                new_w = min(current_weight + self.ADJUSTMENT_STEP, self.MAX_WEIGHT)
                if new_w != current_weight:
                    new_weights[weight_key] = new_w
                    adjustments_made.append(
                        f"{signal_name} ↑ ({current_weight:.2f}→{new_w:.2f}) "
                        f"accuracy={accuracy:.1%}"
                    )

            elif accuracy < self.LOW_ACCURACY_THRESHOLD:
                # Signal performing poorly — decrease its weight
                new_w = max(current_weight - self.ADJUSTMENT_STEP, self.MIN_WEIGHT)
                if new_w != current_weight:
                    new_weights[weight_key] = new_w
                    adjustments_made.append(
                        f"{signal_name} ↓ ({current_weight:.2f}→{new_w:.2f}) "
                        f"accuracy={accuracy:.1%}"
                    )

        if not adjustments_made:
            return None  # no adjustment needed

        # Renormalize so weights sum to 1.0
        total = sum(new_weights.values())
        if total > 0:
            new_weights = {k: round(v / total, 4) for k, v in new_weights.items()}

        # Validate bounds after normalization
        for k in new_weights:
            new_weights[k] = max(self.MIN_WEIGHT, min(self.MAX_WEIGHT, new_weights[k]))

        # Final renormalize after bound clamping
        total = sum(new_weights.values())
        if total > 0:
            new_weights = {k: round(v / total, 4) for k, v in new_weights.items()}

        # Persist the new weights
        self._save_weights(category, new_weights)

        # Build reason string
        reason = (
            f"Auto-adjustment after {len(trades)} trades in {category}. "
            + "; ".join(adjustments_made)
            + f". Accuracies: TA={ta_accuracy:.1%}, "
            f"Sentiment={sentiment_accuracy:.1%}, Speed={speed_accuracy:.1%}."
        )

        # Log the adjustment
        self._log_adjustment(
            category=category,
            old_weights=current_weights,
            new_weights=new_weights,
            reason=reason,
        )

        return {
            "category": category,
            "old_weights": current_weights,
            "new_weights": new_weights,
            "reason": reason,
            "accuracies": accuracies,
        }

    # ------------------------------------------------------------------ #
    # Signal accuracy computation
    # ------------------------------------------------------------------ #

    def _compute_signal_accuracy(self, trades: list, score_field: str) -> float:
        """
        Calculate how accurate a signal type was across a set of closed trades.

        A signal is "accurate" if:
          - The signal score > 50 (bullish prediction) AND the trade was profitable
          - OR the signal score <= 50 (bearish prediction) AND the trade was a loss
            (meaning the market moved against the position — in which case a bearish
            signal would have correctly predicted not to trade, but we approximate
            by treating "bearish on YES" as a signal pointing toward NO position)

        Args:
            trades: List of sqlite3.Row closed trade records
            score_field: Column name in signal_breakdown ('ta_score', 'sentiment_score', 'speed_score')

        Returns:
            float: Accuracy fraction in [0.0, 1.0]
        """
        if not trades:
            return 0.5

        correct = 0
        evaluable = 0

        for row in trades:
            pnl = row["pnl"] if row["pnl"] is not None else 0.0
            breakdown_str = row["signal_breakdown"] or "{}"

            try:
                breakdown = json.loads(breakdown_str)
            except (json.JSONDecodeError, TypeError):
                continue

            signal_score = breakdown.get(score_field, 50.0)

            # Determine what the signal predicted
            signal_predicted_bullish = signal_score > 50
            trade_direction = row["direction"]  # 'YES' or 'NO'
            trade_was_profitable = pnl > 0

            # Signal accuracy: did it agree with the profitable outcome?
            # YES trade profitable AND signal was bullish = correct
            # NO trade profitable AND signal was bearish = correct
            # YES trade loss AND signal was bearish = correct (signal was right to be cautious)
            if trade_direction == "YES":
                is_correct = (signal_predicted_bullish and trade_was_profitable) or (
                    not signal_predicted_bullish and not trade_was_profitable
                )
            else:  # NO trade
                is_correct = (not signal_predicted_bullish and trade_was_profitable) or (
                    signal_predicted_bullish and not trade_was_profitable
                )

            correct += 1 if is_correct else 0
            evaluable += 1

        if evaluable == 0:
            return 0.5

        return correct / evaluable

    # ------------------------------------------------------------------ #
    # DB helpers
    # ------------------------------------------------------------------ #

    def _get_closed_trade_count(self, category: str) -> int:
        """Total closed trades for a category."""
        rows = execute_query(
            """SELECT COUNT(*) as cnt FROM trades t
               JOIN markets m ON t.market_id = m.id
               WHERE t.status = 'closed' AND m.category = ?""",
            (category,),
        )
        return rows[0]["cnt"] if rows else 0

    def _get_recent_closed_trades(self, category: str, limit: int) -> list:
        """Return the most recent closed trades for a category."""
        return execute_query(
            """SELECT t.*, m.category FROM trades t
               JOIN markets m ON t.market_id = m.id
               WHERE t.status = 'closed' AND m.category = ?
               ORDER BY t.exit_time DESC LIMIT ?""",
            (category, limit),
        )

    def _save_weights(self, category: str, weights: dict) -> None:
        """Persist updated strategy weights to the database."""
        now = datetime.now(timezone.utc).isoformat()
        execute_write(
            """INSERT INTO strategy_weights
               (category, ta_weight, sentiment_weight, speed_weight, updated_at)
               VALUES (?, ?, ?, ?, ?)""",
            (
                category,
                weights.get("ta", 0.33),
                weights.get("sentiment", 0.33),
                weights.get("speed", 0.34),
                now,
            ),
        )

    def _log_adjustment(
        self,
        category: str,
        old_weights: dict,
        new_weights: dict,
        reason: str,
    ) -> None:
        """Record an agent adjustment to the agent_log table."""
        now = datetime.now(timezone.utc).isoformat()
        execute_write(
            """INSERT INTO agent_log
               (action, category, old_value, new_value, reason, timestamp)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                "weight_adjustment",
                category,
                json.dumps(old_weights),
                json.dumps(new_weights),
                reason,
                now,
            ),
        )

    def get_adjustment_history(self, limit: int = 50) -> list:
        """Return recent agent adjustments for dashboard display."""
        return execute_query(
            """SELECT * FROM agent_log
               ORDER BY timestamp DESC LIMIT ?""",
            (limit,),
        )
