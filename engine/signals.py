"""
engine/signals.py — Composite signal aggregation and scoring.

Combines TA, sentiment, and speed scores into a single composite score
using configurable per-category weights. Handles all DB persistence for
signals and composite scores.

Composite formula:
    final_score = (ta_score * ta_weight) + (sentiment_score * sentiment_weight) + (speed_score * speed_weight)

The bot only trades when:
  1. final_score >= trade_threshold (default 65)
  2. At least 2 of 3 signal types agree on direction (bullish or bearish)

Usage:
    aggregator = SignalAggregator()
    result = aggregator.compute_composite_score(
        market_id="kalshi:KXBTC-25DEC",
        category="crypto",
        ta_result={"ta_score": 72, "direction": "bullish", ...},
        sentiment_result={"sentiment_score": 61, "direction": "bullish", ...},
        speed_result={"speed_score": 80, "direction": "bullish", ...},
    )
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

from config import config
from database.connection import execute_write, get_db
from database.schema import get_current_weights


class SignalAggregator:
    """
    Aggregates TA, sentiment, and speed signals into a composite trading decision.
    Persists all signal data and composite scores to the database.
    """

    def __init__(self) -> None:
        pass

    def compute_composite_score(
        self,
        market_id: str,
        category: str,
        ta_result: dict,
        sentiment_result: dict,
        speed_result: dict,
    ) -> dict:
        """
        Compute the composite signal score for a potential trade.

        Args:
            market_id: Market identifier
            category: 'sports' | 'crypto' | 'weather'
            ta_result: Output from TechnicalAnalyzer.analyze()
            sentiment_result: Output from SentimentAnalyzer.analyze_market()
            speed_result: Output from SpeedMonitor.compute_speed_score()

        Returns:
            {
                'market_id': str,
                'final_score': float,       # 0-100
                'ta_score': float,
                'sentiment_score': float,
                'speed_score': float,
                'ta_weight': float,
                'sentiment_weight': float,
                'speed_weight': float,
                'recommendation': str,      # 'BUY_YES' | 'BUY_NO' | 'HOLD'
                'direction': str,           # 'bullish' | 'bearish' | 'neutral'
                'signals_agreeing': int,    # 0-3 signal types in agreement
                'trade_eligible': bool,     # True if score >= threshold and ≥2 agree
            }
        """
        ta_score = float(ta_result.get("ta_score", 50.0))
        sentiment_score = float(sentiment_result.get("sentiment_score", 50.0))
        speed_score = float(speed_result.get("speed_score", 50.0))

        ta_direction = ta_result.get("direction", "neutral")
        sentiment_direction = sentiment_result.get("direction", "neutral")
        speed_direction = speed_result.get("direction", "neutral")

        # Get current weights (may have been adjusted by agent)
        weights = get_current_weights(category)
        ta_w = weights["ta"]
        sentiment_w = weights["sentiment"]
        speed_w = weights["speed"]

        # Composite score
        final_score = (
            ta_score * ta_w
            + sentiment_score * sentiment_w
            + speed_score * speed_w
        )
        final_score = round(max(0.0, min(100.0, final_score)), 2)

        # Direction consensus — how many signal types agree?
        directions = [ta_direction, sentiment_direction, speed_direction]
        bullish_count = directions.count("bullish")
        bearish_count = directions.count("bearish")

        if bullish_count >= 2:
            consensus_direction = "bullish"
            signals_agreeing = bullish_count
        elif bearish_count >= 2:
            consensus_direction = "bearish"
            signals_agreeing = bearish_count
        else:
            consensus_direction = "neutral"
            signals_agreeing = 1

        # Trading recommendation
        threshold = config.trade_threshold
        trade_eligible = (final_score >= threshold) and (signals_agreeing >= 2)

        if trade_eligible and consensus_direction == "bullish":
            recommendation = "BUY_YES"
        elif trade_eligible and consensus_direction == "bearish":
            recommendation = "BUY_NO"
        else:
            recommendation = "HOLD"

        return {
            "market_id": market_id,
            "final_score": final_score,
            "ta_score": ta_score,
            "sentiment_score": sentiment_score,
            "speed_score": speed_score,
            "ta_weight": ta_w,
            "sentiment_weight": sentiment_w,
            "speed_weight": speed_w,
            "recommendation": recommendation,
            "direction": consensus_direction,
            "signals_agreeing": signals_agreeing,
            "trade_eligible": trade_eligible,
            "signal_breakdown": {
                "ta": {"score": ta_score, "direction": ta_direction},
                "sentiment": {"score": sentiment_score, "direction": sentiment_direction},
                "speed": {"score": speed_score, "direction": speed_direction},
            },
        }

    # ------------------------------------------------------------------ #
    # Database persistence
    # ------------------------------------------------------------------ #

    def save_signal(
        self,
        market_id: str,
        signal_type: str,
        signal_name: str,
        value: float,
        direction: str = "neutral",
        confidence: float = 0.0,
        metadata: Optional[dict] = None,
        acted_on: bool = False,
    ) -> int:
        """
        Persist a signal to the `signals` table.
        Returns the new row ID.
        """
        ts = datetime.now(timezone.utc).isoformat()
        meta_json = json.dumps(metadata) if metadata else "{}"
        return execute_write(
            """INSERT INTO signals
               (market_id, signal_type, signal_name, value, direction,
                confidence, metadata, acted_on, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                market_id,
                signal_type,
                signal_name,
                value,
                direction,
                confidence,
                meta_json,
                1 if acted_on else 0,
                ts,
            ),
        )

    def save_composite_score(self, score_dict: dict) -> int:
        """
        Persist a composite score to the `composite_scores` table.
        Returns the new row ID.
        """
        ts = datetime.now(timezone.utc).isoformat()
        return execute_write(
            """INSERT INTO composite_scores
               (market_id, ta_score, sentiment_score, speed_score,
                ta_weight, sentiment_weight, speed_weight,
                final_score, recommendation, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                score_dict["market_id"],
                score_dict["ta_score"],
                score_dict["sentiment_score"],
                score_dict["speed_score"],
                score_dict["ta_weight"],
                score_dict["sentiment_weight"],
                score_dict["speed_weight"],
                score_dict["final_score"],
                score_dict["recommendation"],
                ts,
            ),
        )

    def save_all_signals(self, market_id: str, composite: dict) -> None:
        """
        Convenience method: save all three individual signals plus
        the composite score in a single call.
        """
        breakdown = composite.get("signal_breakdown", {})

        if "ta" in breakdown:
            self.save_signal(
                market_id=market_id,
                signal_type="ta",
                signal_name="ta_composite",
                value=breakdown["ta"]["score"],
                direction=breakdown["ta"]["direction"],
                confidence=breakdown["ta"]["score"],
                metadata={
                    "breakout_state": composite.get("ta_breakout_state", "SCANNING"),
                },
                acted_on=(composite["recommendation"] != "HOLD"),
            )

        if "sentiment" in breakdown:
            self.save_signal(
                market_id=market_id,
                signal_type="sentiment",
                signal_name="sentiment_composite",
                value=breakdown["sentiment"]["score"],
                direction=breakdown["sentiment"]["direction"],
                confidence=breakdown["sentiment"]["score"],
                acted_on=(composite["recommendation"] != "HOLD"),
            )

        if "speed" in breakdown:
            self.save_signal(
                market_id=market_id,
                signal_type="speed",
                signal_name="speed_composite",
                value=breakdown["speed"]["score"],
                direction=breakdown["speed"]["direction"],
                confidence=breakdown["speed"]["score"],
                acted_on=(composite["recommendation"] != "HOLD"),
            )

        self.save_composite_score(composite)

    # ------------------------------------------------------------------ #
    # Log helper
    # ------------------------------------------------------------------ #

    @staticmethod
    def log_to_db(level: str, module: str, message: str) -> None:
        """Write a message to the bot_log table."""
        ts = datetime.now(timezone.utc).isoformat()
        try:
            execute_write(
                "INSERT INTO bot_log (level, module, message, timestamp) VALUES (?, ?, ?, ?)",
                (level, module, message, ts),
            )
        except Exception:
            pass  # never let logging crash the engine
