"""
analysis/technical.py — Technical analysis engine for prediction market price data.

Analyzes OHLCV candlestick data for each prediction market and produces
a TA confidence score (0-100) with directional bias.

Primary signal: Double-Breakout State Machine
  SCANNING → CONSOLIDATION_DETECTED → FIRST_BREAKOUT → RETEST → SECOND_BREAKOUT_SIGNAL

Secondary signals: SMA/EMA crossovers, VWAP deviation, volume spikes.

All prices are in [0.0, 1.0] range (prediction market contract pricing).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional


class BreakoutState(Enum):
    """State machine states for the double-breakout pattern detector."""

    SCANNING = "SCANNING"
    CONSOLIDATION_DETECTED = "CONSOLIDATION_DETECTED"
    FIRST_BREAKOUT = "FIRST_BREAKOUT"
    RETEST = "RETEST"
    SECOND_BREAKOUT_SIGNAL = "SECOND_BREAKOUT_SIGNAL"


@dataclass
class BreakoutMachine:
    """
    Per-market state machine that tracks the double-breakout pattern.

    The double-breakout is the primary TA signal:
      1. Price consolidates in a tight range for N candles
      2. Price breaks out of the range
      3. Price pulls back to re-test the breakout level
      4. Price breaks out again in the same direction (SIGNAL)

    This "failed retest then successful second breakout" pattern is a
    reliable continuation signal in prediction markets because it shows
    that participants who faded the first breakout have capitulated.
    """

    # Configuration thresholds
    CONSOLIDATION_MIN_CANDLES: int = 5
    CONSOLIDATION_MAX_RANGE_PCT: float = 0.03   # 3% range qualifies as consolidation
    BREAKOUT_MIN_PCT: float = 0.015             # 1.5% move = breakout
    RETEST_TOLERANCE_PCT: float = 0.015         # how far from breakout level = retest
    TIMEOUT_CANDLES: int = 50                   # reset after this many candles

    # Current state
    state: BreakoutState = BreakoutState.SCANNING
    breakout_direction: str = "neutral"          # 'bullish' | 'bearish'
    consolidation_high: float = 0.0
    consolidation_low: float = 0.0
    first_breakout_price: float = 0.0
    candles_in_state: int = 0
    recent_volumes: List[float] = field(default_factory=list)

    def update(self, candle: dict) -> tuple[BreakoutState, float]:
        """
        Process one new candle and return the updated state + confidence score.

        Args:
            candle: dict with keys: open, high, low, close, volume

        Returns:
            (new_state, confidence_score 0-100)
        """
        close = candle["close"]
        high = candle["high"]
        low = candle["low"]
        volume = candle.get("volume", 0.0)

        self.candles_in_state += 1
        self.recent_volumes.append(volume)
        if len(self.recent_volumes) > 20:
            self.recent_volumes.pop(0)

        avg_volume = sum(self.recent_volumes) / max(len(self.recent_volumes), 1)

        # Timeout guard — reset if stuck too long in a non-terminal state
        if self.candles_in_state > self.TIMEOUT_CANDLES and self.state not in (
            BreakoutState.SCANNING,
            BreakoutState.SECOND_BREAKOUT_SIGNAL,
        ):
            self._reset()
            return self.state, 0.0

        if self.state == BreakoutState.SCANNING:
            return self.state, 0.0  # accumulating data, no score yet

        elif self.state == BreakoutState.CONSOLIDATION_DETECTED:
            # Check for breakout
            if close > self.consolidation_high * (1 + self.BREAKOUT_MIN_PCT):
                self.state = BreakoutState.FIRST_BREAKOUT
                self.first_breakout_price = close
                self.breakout_direction = "bullish"
                self.candles_in_state = 0
                return self.state, 35.0  # modest confidence for first breakout alone

            elif close < self.consolidation_low * (1 - self.BREAKOUT_MIN_PCT):
                self.state = BreakoutState.FIRST_BREAKOUT
                self.first_breakout_price = close
                self.breakout_direction = "bearish"
                self.candles_in_state = 0
                return self.state, 35.0

            return self.state, 0.0

        elif self.state == BreakoutState.FIRST_BREAKOUT:
            # Look for a retest of the breakout level
            breakout_level = (
                self.consolidation_high
                if self.breakout_direction == "bullish"
                else self.consolidation_low
            )
            distance_pct = abs(close - breakout_level) / max(breakout_level, 0.001)

            if distance_pct <= self.RETEST_TOLERANCE_PCT:
                # Price has returned to retest the breakout level
                self.state = BreakoutState.RETEST
                self.candles_in_state = 0
                return self.state, 20.0

            # Check for invalidation (breakout in opposite direction)
            if self.breakout_direction == "bullish":
                if close < self.consolidation_low * (1 - self.BREAKOUT_MIN_PCT):
                    self._reset()
                    return self.state, 0.0
            else:
                if close > self.consolidation_high * (1 + self.BREAKOUT_MIN_PCT):
                    self._reset()
                    return self.state, 0.0

            return self.state, 25.0

        elif self.state == BreakoutState.RETEST:
            # Look for second breakout in the same direction
            if self.breakout_direction == "bullish":
                if close > self.consolidation_high * (1 + self.BREAKOUT_MIN_PCT):
                    # SIGNAL: second bullish breakout confirmed
                    confidence = 75.0
                    if volume > avg_volume * 1.5:
                        confidence += 15.0   # volume confirmation
                    self.state = BreakoutState.SECOND_BREAKOUT_SIGNAL
                    self.candles_in_state = 0
                    return self.state, min(confidence, 100.0)

                # Invalidation: collapses through consolidation low
                if close < self.consolidation_low * (1 - self.BREAKOUT_MIN_PCT):
                    self._reset()
                    return self.state, 0.0

            else:  # bearish
                if close < self.consolidation_low * (1 - self.BREAKOUT_MIN_PCT):
                    # SIGNAL: second bearish breakout confirmed
                    confidence = 75.0
                    if volume > avg_volume * 1.5:
                        confidence += 15.0
                    self.state = BreakoutState.SECOND_BREAKOUT_SIGNAL
                    self.candles_in_state = 0
                    return self.state, min(confidence, 100.0)

                # Invalidation
                if close > self.consolidation_high * (1 + self.BREAKOUT_MIN_PCT):
                    self._reset()
                    return self.state, 0.0

            return self.state, 30.0

        elif self.state == BreakoutState.SECOND_BREAKOUT_SIGNAL:
            # Hold signal for a few candles, then reset back to scanning
            if self.candles_in_state > 3:
                self._reset()
            return self.state, 90.0  # high confidence while in signal state

        return self.state, 0.0

    def try_detect_consolidation(self, candles: List[dict]) -> bool:
        """
        Analyze a window of recent candles to detect a consolidation box.
        Returns True if consolidation was detected and state was updated.
        """
        if len(candles) < self.CONSOLIDATION_MIN_CANDLES:
            return False

        recent = candles[-self.CONSOLIDATION_MIN_CANDLES :]
        highest_high = max(c["high"] for c in recent)
        lowest_low = min(c["low"] for c in recent)
        mid_price = (highest_high + lowest_low) / 2

        if mid_price <= 0:
            return False

        range_pct = (highest_high - lowest_low) / mid_price

        if range_pct <= self.CONSOLIDATION_MAX_RANGE_PCT:
            self.state = BreakoutState.CONSOLIDATION_DETECTED
            self.consolidation_high = highest_high
            self.consolidation_low = lowest_low
            self.candles_in_state = 0
            return True

        return False

    def _reset(self) -> None:
        """Reset to SCANNING state."""
        self.state = BreakoutState.SCANNING
        self.breakout_direction = "neutral"
        self.consolidation_high = 0.0
        self.consolidation_low = 0.0
        self.first_breakout_price = 0.0
        self.candles_in_state = 0


class TechnicalAnalyzer:
    """
    Main TA engine. Maintains per-market state machines and computes
    TA scores from candlestick data.

    Usage:
        analyzer = TechnicalAnalyzer()
        result = analyzer.analyze("kalshi:KXBTC-25DEC", candles)
        # result['ta_score'] -> float 0-100
        # result['direction'] -> 'bullish' | 'bearish' | 'neutral'
    """

    def __init__(self) -> None:
        # One state machine per market
        self._machines: Dict[str, BreakoutMachine] = {}

    def _get_machine(self, market_id: str) -> BreakoutMachine:
        """Get or create the breakout machine for a market."""
        if market_id not in self._machines:
            self._machines[market_id] = BreakoutMachine()
        return self._machines[market_id]

    # ------------------------------------------------------------------ #
    # Indicator calculations (static-style, operate on price lists)
    # ------------------------------------------------------------------ #

    @staticmethod
    def sma(prices: List[float], period: int) -> float:
        """Simple moving average of the last `period` prices."""
        if not prices:
            return 0.0
        window = prices[-period:] if len(prices) >= period else prices
        return sum(window) / len(window)

    @staticmethod
    def ema(prices: List[float], period: int) -> float:
        """
        Exponential moving average using the standard smoothing factor k=2/(period+1).
        Seeded with the first price value.
        """
        if not prices:
            return 0.0
        if len(prices) == 1:
            return prices[0]
        k = 2.0 / (period + 1)
        ema_val = prices[0]
        for price in prices[1:]:
            ema_val = price * k + ema_val * (1.0 - k)
        return ema_val

    @staticmethod
    def vwap(candles: List[dict]) -> float:
        """
        Volume-weighted average price.
        Typical price = (high + low + close) / 3
        """
        if not candles:
            return 0.0
        total_volume = sum(c.get("volume", 0.0) for c in candles)
        if total_volume <= 0:
            # No volume data — return simple average of close prices
            return sum(c["close"] for c in candles) / len(candles)

        numerator = sum(
            ((c["high"] + c["low"] + c["close"]) / 3.0) * c.get("volume", 0.0)
            for c in candles
        )
        return numerator / total_volume

    @staticmethod
    def volume_spike_ratio(candles: List[dict], lookback: int = 20) -> float:
        """
        Ratio of the most recent candle's volume vs the average of the previous
        `lookback` candles. Ratio > 2.0 indicates a significant volume spike.
        """
        if len(candles) < 2:
            return 1.0
        recent_vol = candles[-1].get("volume", 0.0)
        baseline_candles = candles[-lookback - 1 : -1]
        if not baseline_candles:
            return 1.0
        baseline_avg = sum(c.get("volume", 0.0) for c in baseline_candles) / len(baseline_candles)
        if baseline_avg <= 0:
            return 1.0
        return recent_vol / baseline_avg

    @staticmethod
    def orderbook_imbalance(yes_bid_volume: float, no_bid_volume: float) -> float:
        """
        Orderbook imbalance score from -1 (full NO pressure) to +1 (full YES pressure).
        In prediction markets, YES bids and NO bids compete directly.
        """
        total = yes_bid_volume + no_bid_volume
        if total <= 0:
            return 0.0
        return (yes_bid_volume - no_bid_volume) / total

    # ------------------------------------------------------------------ #
    # Main analysis function
    # ------------------------------------------------------------------ #

    def analyze(
        self,
        market_id: str,
        candles: List[dict],
        yes_bid_volume: float = 0.0,
        no_bid_volume: float = 0.0,
    ) -> dict:
        """
        Run full TA analysis on a market and return a score dict.

        Args:
            market_id: Unique market identifier
            candles: List of OHLCV dicts (oldest first), price range [0, 1]
            yes_bid_volume: Total YES bid volume from orderbook
            no_bid_volume: Total NO bid volume from orderbook

        Returns:
            {
                'ta_score': float,          # 0-100
                'direction': str,           # 'bullish' | 'bearish' | 'neutral'
                'sma_10': float,
                'ema_60': float,
                'vwap': float,
                'volume_spike_ratio': float,
                'breakout_state': str,
                'breakout_direction': str,
                'breakout_confidence': float,
                'orderbook_imbalance': float,
                'candle_count': int,
            }
        """
        if not candles:
            return self._neutral_result(market_id)

        closes = [c["close"] for c in candles]
        current_price = closes[-1]

        # ---- Indicators ----
        sma_10 = self.sma(closes, 10)
        ema_60 = self.ema(closes, 60)
        vwap_val = self.vwap(candles)
        vol_spike = self.volume_spike_ratio(candles)
        ob_imbalance = self.orderbook_imbalance(yes_bid_volume, no_bid_volume)

        # ---- Breakout state machine ----
        machine = self._get_machine(market_id)

        # Try to detect a new consolidation if in scanning state
        if machine.state == BreakoutState.SCANNING and len(candles) >= machine.CONSOLIDATION_MIN_CANDLES:
            machine.try_detect_consolidation(candles)

        # Feed the latest candle into the machine
        machine_state, breakout_confidence = machine.update(candles[-1])

        # ---- Score assembly ----
        score = 50.0  # neutral baseline

        # SMA/EMA trend direction
        if current_price > sma_10:
            score += 5.0
        else:
            score -= 5.0

        if sma_10 > ema_60:
            score += 8.0   # short-term trend above medium-term = bullish
        else:
            score -= 8.0

        # VWAP deviation
        if vwap_val > 0:
            vwap_pct_diff = (current_price - vwap_val) / vwap_val
            score += vwap_pct_diff * 50   # scale: 5% above VWAP = +2.5 points

        # Volume spike confirmation
        if vol_spike > 2.0:
            score += 10.0  # significant volume, something is happening
        elif vol_spike > 1.5:
            score += 5.0

        # Orderbook imbalance (scaled: +1 imbalance = +10 points)
        score += ob_imbalance * 10.0

        # Breakout pattern (dominant signal when present)
        if machine_state == BreakoutState.SECOND_BREAKOUT_SIGNAL:
            if machine.breakout_direction == "bullish":
                score += breakout_confidence * 0.3
            else:
                score -= breakout_confidence * 0.3
        elif machine_state in (BreakoutState.FIRST_BREAKOUT, BreakoutState.RETEST):
            if machine.breakout_direction == "bullish":
                score += breakout_confidence * 0.15
            else:
                score -= breakout_confidence * 0.15

        # Clamp to [0, 100]
        score = max(0.0, min(100.0, score))

        # Determine directional bias
        if score > 55:
            direction = "bullish"
        elif score < 45:
            direction = "bearish"
        else:
            direction = "neutral"

        return {
            "ta_score": round(score, 2),
            "direction": direction,
            "sma_10": round(sma_10, 4),
            "ema_60": round(ema_60, 4),
            "vwap": round(vwap_val, 4),
            "volume_spike_ratio": round(vol_spike, 2),
            "breakout_state": machine_state.value,
            "breakout_direction": machine.breakout_direction,
            "breakout_confidence": round(breakout_confidence, 2),
            "orderbook_imbalance": round(ob_imbalance, 3),
            "candle_count": len(candles),
        }

    def _neutral_result(self, market_id: str) -> dict:
        """Return a neutral analysis result when no data is available."""
        machine = self._get_machine(market_id)
        return {
            "ta_score": 50.0,
            "direction": "neutral",
            "sma_10": 0.0,
            "ema_60": 0.0,
            "vwap": 0.0,
            "volume_spike_ratio": 1.0,
            "breakout_state": machine.state.value,
            "breakout_direction": "neutral",
            "breakout_confidence": 0.0,
            "orderbook_imbalance": 0.0,
            "candle_count": 0,
        }

    def get_state(self, market_id: str) -> BreakoutState:
        """Return the current breakout state for a market."""
        return self._get_machine(market_id).state

    def reset_market(self, market_id: str) -> None:
        """Reset the state machine for a market (e.g., after resolution)."""
        if market_id in self._machines:
            del self._machines[market_id]
