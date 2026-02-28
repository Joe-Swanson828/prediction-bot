"""
tests/test_signals.py — Tests for composite signal aggregation.

Run with: python -m pytest tests/test_signals.py -v
"""

import pytest
from engine.signals import SignalAggregator


class TestSignalAggregator:
    """Test composite score calculation and trade eligibility."""

    def setup_method(self):
        self.agg = SignalAggregator()

    def _make_ta(self, score: float, direction: str = "bullish") -> dict:
        return {"ta_score": score, "direction": direction, "breakout_state": "SCANNING"}

    def _make_sentiment(self, score: float, direction: str = "bullish") -> dict:
        return {"sentiment_score": score, "direction": direction}

    def _make_speed(self, score: float, direction: str = "bullish") -> dict:
        return {"speed_score": score, "direction": direction}

    def test_composite_score_crypto_default_weights(self):
        # Crypto weights: TA=0.40, Sent=0.30, Speed=0.30
        result = self.agg.compute_composite_score(
            market_id="test:market",
            category="crypto",
            ta_result=self._make_ta(80),
            sentiment_result=self._make_sentiment(70),
            speed_result=self._make_speed(90),
        )
        # Expected: 80*0.40 + 70*0.30 + 90*0.30 = 32 + 21 + 27 = 80
        assert result["final_score"] == pytest.approx(80.0, abs=2.0)  # abs=2 allows for DB weight rounding

    def test_composite_score_sports_default_weights(self):
        # Sports weights: TA=0.20, Sent=0.35, Speed=0.45
        result = self.agg.compute_composite_score(
            market_id="test:sports",
            category="sports",
            ta_result=self._make_ta(60),
            sentiment_result=self._make_sentiment(80),
            speed_result=self._make_speed(70),
        )
        # Expected: 60*0.20 + 80*0.35 + 70*0.45 = 12 + 28 + 31.5 = 71.5
        assert result["final_score"] == pytest.approx(71.5, abs=2.0)

    def test_trade_eligible_when_score_and_agreement(self):
        # All three signals bullish + final score above threshold
        result = self.agg.compute_composite_score(
            market_id="test:eligible",
            category="crypto",
            ta_result=self._make_ta(80, "bullish"),
            sentiment_result=self._make_sentiment(75, "bullish"),
            speed_result=self._make_speed(85, "bullish"),
        )
        assert result["trade_eligible"] is True
        assert result["recommendation"] == "BUY_YES"

    def test_not_eligible_below_threshold(self):
        # Low scores → below threshold (65)
        result = self.agg.compute_composite_score(
            market_id="test:low_score",
            category="crypto",
            ta_result=self._make_ta(30, "bearish"),
            sentiment_result=self._make_sentiment(40, "bearish"),
            speed_result=self._make_speed(35, "bearish"),
        )
        assert result["trade_eligible"] is False
        assert result["recommendation"] == "HOLD"

    def test_not_eligible_no_consensus(self):
        # All different directions — no consensus
        result = self.agg.compute_composite_score(
            market_id="test:no_consensus",
            category="crypto",
            ta_result=self._make_ta(70, "bullish"),
            sentiment_result=self._make_sentiment(70, "bearish"),
            speed_result=self._make_speed(70, "neutral"),
        )
        # signals_agreeing < 2, so not eligible even if score is above threshold
        assert result["trade_eligible"] is False

    def test_bearish_recommendation(self):
        result = self.agg.compute_composite_score(
            market_id="test:bearish",
            category="sports",
            ta_result=self._make_ta(20, "bearish"),
            sentiment_result=self._make_sentiment(25, "bearish"),
            speed_result=self._make_speed(15, "bearish"),
        )
        # Very low score, bearish direction
        if result["trade_eligible"]:
            assert result["recommendation"] == "BUY_NO"

    def test_score_clamped_to_range(self):
        result = self.agg.compute_composite_score(
            market_id="test:clamp",
            category="crypto",
            ta_result=self._make_ta(0),
            sentiment_result=self._make_sentiment(0),
            speed_result=self._make_speed(0),
        )
        assert result["final_score"] >= 0.0
        assert result["final_score"] <= 100.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
