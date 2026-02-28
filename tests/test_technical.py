"""
tests/test_technical.py — Tests for the TA engine and double-breakout state machine.

Run with: python -m pytest tests/test_technical.py -v
"""

import pytest
from analysis.technical import BreakoutMachine, BreakoutState, TechnicalAnalyzer


def make_candle(close: float, volume: float = 100.0) -> dict:
    """Helper: create a candle dict."""
    spread = 0.002
    return {
        "open": close,
        "high": close + spread,
        "low": close - spread,
        "close": close,
        "volume": volume,
    }


def make_flat_candles(price: float, count: int = 7, volume: float = 100.0) -> list:
    """Helper: create N candles at roughly the same price (consolidation)."""
    import random
    candles = []
    for _ in range(count):
        p = price + random.uniform(-0.005, 0.005)
        candles.append({
            "open": p,
            "high": p + 0.003,
            "low": p - 0.003,
            "close": p,
            "volume": volume,
        })
    return candles


class TestIndicators:
    """Test SMA, EMA, and VWAP calculations."""

    def test_sma_basic(self):
        ta = TechnicalAnalyzer()
        prices = [1.0, 2.0, 3.0, 4.0, 5.0]
        assert ta.sma(prices, 5) == pytest.approx(3.0)

    def test_sma_partial(self):
        ta = TechnicalAnalyzer()
        prices = [1.0, 2.0]
        # Less than period — should use available data
        result = ta.sma(prices, 5)
        assert result == pytest.approx(1.5)

    def test_sma_empty(self):
        ta = TechnicalAnalyzer()
        assert ta.sma([], 5) == 0.0

    def test_ema_constant_input(self):
        ta = TechnicalAnalyzer()
        prices = [0.5] * 20
        result = ta.ema(prices, 9)
        assert result == pytest.approx(0.5, abs=0.001)

    def test_vwap_no_volume(self):
        ta = TechnicalAnalyzer()
        candles = [{"high": 0.6, "low": 0.5, "close": 0.55, "volume": 0} for _ in range(5)]
        result = ta.vwap(candles)
        # No volume data → simple average of close prices
        assert result == pytest.approx(0.55, abs=0.001)

    def test_volume_spike_no_spike(self):
        ta = TechnicalAnalyzer()
        candles = [{"volume": 100} for _ in range(20)] + [{"volume": 110}]
        ratio = ta.volume_spike_ratio(candles)
        assert 1.0 <= ratio <= 1.5

    def test_volume_spike_detected(self):
        ta = TechnicalAnalyzer()
        candles = [{"volume": 100} for _ in range(20)] + [{"volume": 500}]
        ratio = ta.volume_spike_ratio(candles)
        assert ratio > 4.0

    def test_orderbook_imbalance(self):
        ta = TechnicalAnalyzer()
        assert ta.orderbook_imbalance(100, 0) == pytest.approx(1.0)
        assert ta.orderbook_imbalance(0, 100) == pytest.approx(-1.0)
        assert ta.orderbook_imbalance(50, 50) == pytest.approx(0.0)
        assert ta.orderbook_imbalance(0, 0) == pytest.approx(0.0)


class TestBreakoutStateMachine:
    """Test the double-breakout state machine transitions."""

    def test_initial_state_is_scanning(self):
        machine = BreakoutMachine()
        assert machine.state == BreakoutState.SCANNING

    def test_detect_consolidation(self):
        machine = BreakoutMachine()
        candles = make_flat_candles(0.50, count=7)
        detected = machine.try_detect_consolidation(candles)
        assert detected is True
        assert machine.state == BreakoutState.CONSOLIDATION_DETECTED

    def test_not_enough_candles_for_consolidation(self):
        machine = BreakoutMachine()
        candles = make_flat_candles(0.50, count=3)  # less than minimum 5
        detected = machine.try_detect_consolidation(candles)
        assert detected is False
        assert machine.state == BreakoutState.SCANNING

    def test_bullish_breakout(self):
        machine = BreakoutMachine()
        machine.state = BreakoutState.CONSOLIDATION_DETECTED
        machine.consolidation_high = 0.55
        machine.consolidation_low = 0.45

        # Candle breaks above consolidation_high * 1.015
        breakout_candle = make_candle(0.56)  # above 0.55 * 1.015 ≈ 0.558
        state, confidence = machine.update(breakout_candle)
        assert state == BreakoutState.FIRST_BREAKOUT
        assert machine.breakout_direction == "bullish"

    def test_retest_after_breakout(self):
        machine = BreakoutMachine()
        machine.state = BreakoutState.FIRST_BREAKOUT
        machine.breakout_direction = "bullish"
        machine.consolidation_high = 0.55
        machine.consolidation_low = 0.45
        machine.first_breakout_price = 0.57

        # Price returns near the breakout level (within RETEST_TOLERANCE_PCT)
        retest_candle = make_candle(0.553)  # within ~1.5% of 0.55
        state, confidence = machine.update(retest_candle)
        assert state == BreakoutState.RETEST

    def test_second_breakout_signal(self):
        machine = BreakoutMachine()
        machine.state = BreakoutState.RETEST
        machine.breakout_direction = "bullish"
        machine.consolidation_high = 0.55
        machine.consolidation_low = 0.45
        machine.recent_volumes = [100.0] * 10

        # Second breakout with high volume
        signal_candle = make_candle(0.565, volume=300)  # above 0.55 * 1.015 with spike
        state, confidence = machine.update(signal_candle)
        assert state == BreakoutState.SECOND_BREAKOUT_SIGNAL
        assert confidence >= 75.0

    def test_reset_on_timeout(self):
        machine = BreakoutMachine()
        machine.state = BreakoutState.FIRST_BREAKOUT
        machine.breakout_direction = "bullish"
        machine.consolidation_high = 0.55
        machine.consolidation_low = 0.45
        machine.candles_in_state = 45

        # Feed 10 more candles at the same price (no breakout or retest)
        for _ in range(10):
            candle = make_candle(0.57)
            state, _ = machine.update(candle)

        assert machine.state == BreakoutState.SCANNING


class TestTechnicalAnalyzer:
    """Test the full TA pipeline."""

    def test_analyze_empty_candles(self):
        ta = TechnicalAnalyzer()
        result = ta.analyze("test:market", [])
        assert result["ta_score"] == 50.0
        assert result["direction"] == "neutral"

    def test_analyze_returns_score_in_range(self):
        ta = TechnicalAnalyzer()
        candles = [make_candle(0.5 + i * 0.001, volume=100) for i in range(50)]
        result = ta.analyze("test:market", candles)
        assert 0.0 <= result["ta_score"] <= 100.0

    def test_analyze_bullish_trend_scores_high(self):
        ta = TechnicalAnalyzer()
        # Rising price series — should produce bullish/above-neutral score
        candles = [make_candle(0.3 + i * 0.004, volume=200) for i in range(30)]
        result = ta.analyze("test:trending", candles)
        assert result["ta_score"] >= 50.0

    def test_get_state_new_market(self):
        ta = TechnicalAnalyzer()
        state = ta.get_state("never:seen:before")
        assert state == BreakoutState.SCANNING

    def test_reset_market(self):
        ta = TechnicalAnalyzer()
        # Analyze a market to create its state
        candles = [make_candle(0.5, volume=100) for _ in range(10)]
        ta.analyze("test:reset", candles)
        ta.reset_market("test:reset")
        # After reset, it should be back to scanning
        assert ta.get_state("test:reset") == BreakoutState.SCANNING


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
