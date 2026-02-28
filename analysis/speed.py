"""
analysis/speed.py — Speed/information advantage signal engine.

Detects situations where the bot has an information edge over the prediction
market pricing. This includes:

  1. Freshness: how recently we've received a price update (stale = lower score)
  2. Volume spikes: sudden volume surges signal informed trading activity
  3. Price momentum: rapid directional price moves in the underlying asset
  4. Cross-source consensus: when multiple data sources agree on an outcome
     that prediction market pricing doesn't reflect yet

Score range: 0-100 (50 = neutral, >60 = favorable edge, >75 = strong signal)

Usage:
    monitor = SpeedMonitor()
    monitor.record_update("kalshi:KXBTC-25DEC", price=0.55, volume=1200)
    result = monitor.compute_speed_score("kalshi:KXBTC-25DEC", "crypto")
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class MarketSpeedData:
    """Per-market data tracked by the speed monitor."""

    market_id: str
    last_update_ts: float = 0.0          # Unix timestamp of last update
    price_history: List[float] = field(default_factory=list)
    volume_history: List[float] = field(default_factory=list)
    consensus_score: Optional[float] = None   # from multi-source consensus
    consensus_direction: str = "neutral"
    consensus_source_count: int = 0

    def record(self, price: float, volume: float) -> None:
        """Record a new price/volume tick."""
        self.last_update_ts = time.time()
        self.price_history.append(price)
        self.volume_history.append(volume)
        # Keep last 30 data points
        if len(self.price_history) > 30:
            self.price_history.pop(0)
        if len(self.volume_history) > 30:
            self.volume_history.pop(0)

    @property
    def staleness_seconds(self) -> float:
        """Seconds since last update. 0 if never updated."""
        if self.last_update_ts == 0:
            return float("inf")
        return time.time() - self.last_update_ts

    @property
    def volume_baseline(self) -> float:
        """Average volume over the history window."""
        if len(self.volume_history) < 2:
            return max(self.volume_history[0], 1.0) if self.volume_history else 1.0
        return sum(self.volume_history[:-1]) / len(self.volume_history[:-1])

    @property
    def latest_volume_spike(self) -> float:
        """Ratio of latest volume vs baseline. 1.0 = normal, >2 = spike."""
        if not self.volume_history:
            return 1.0
        latest = self.volume_history[-1]
        baseline = self.volume_baseline
        return latest / max(baseline, 1.0)

    @property
    def price_momentum(self) -> float:
        """
        Signed price momentum over last N ticks.
        Returns a value in roughly [-0.1, 0.1] for prediction market prices.
        """
        if len(self.price_history) < 3:
            return 0.0
        # Compare latest price vs 3 ticks ago
        return self.price_history[-1] - self.price_history[-3]


class SpeedMonitor:
    """
    Tracks price update freshness, volume spikes, and price momentum
    to generate speed/information advantage scores per market.
    """

    # Scoring thresholds
    FRESHNESS_FULL_SCORE_SECS: float = 5.0    # <5s since update = full freshness bonus
    FRESHNESS_STALE_SECS: float = 120.0        # >120s = fully stale, no freshness
    VOLUME_SPIKE_THRESHOLD: float = 2.0        # >2x baseline = spike signal
    MOMENTUM_SCALE: float = 500.0              # multiply momentum to get score impact

    def __init__(self) -> None:
        self._data: Dict[str, MarketSpeedData] = {}

    def _get_data(self, market_id: str) -> MarketSpeedData:
        """Get or create tracking data for a market."""
        if market_id not in self._data:
            self._data[market_id] = MarketSpeedData(market_id=market_id)
        return self._data[market_id]

    def record_update(self, market_id: str, price: float, volume: float = 0.0) -> None:
        """
        Record a new price tick for a market. Call this whenever a new
        price or orderbook update arrives from the exchange.

        Args:
            market_id: Market identifier
            price: Current YES contract price (0.0-1.0)
            volume: Volume associated with this tick
        """
        data = self._get_data(market_id)
        data.record(price, volume)

    def update_consensus(
        self,
        market_id: str,
        consensus_score: float,
        direction: str,
        source_count: int,
    ) -> None:
        """
        Update the multi-source consensus for a market.

        Called when external data sources (weather APIs, sports feeds,
        crypto exchanges) provide updated information about the underlying
        outcome that the prediction market is pricing.

        Args:
            market_id: Market identifier
            consensus_score: 0-100 probability estimate from external sources
            direction: 'bullish' | 'bearish' | 'neutral' vs current market price
            source_count: Number of sources that agree on this assessment
        """
        data = self._get_data(market_id)
        data.consensus_score = consensus_score
        data.consensus_direction = direction
        data.consensus_source_count = source_count

    def compute_speed_score(
        self,
        market_id: str,
        category: str,
        current_market_price: Optional[float] = None,
    ) -> dict:
        """
        Compute the speed/information advantage score for a market.

        Args:
            market_id: Market identifier
            category: 'sports' | 'crypto' | 'weather'
            current_market_price: Current YES price for consensus comparison

        Returns:
            {
                'speed_score': float,        # 0-100
                'direction': str,            # 'bullish' | 'bearish' | 'neutral'
                'staleness_seconds': float,
                'volume_spike_ratio': float,
                'price_momentum': float,
                'consensus_edge': float,     # divergence vs external sources
                'score_breakdown': dict,     # detailed scoring components
            }
        """
        data = self._get_data(market_id)

        # Start from neutral baseline
        score = 50.0
        direction = "neutral"
        breakdown = {}

        # ---- Component 1: Freshness ----
        staleness = data.staleness_seconds
        if staleness == float("inf") or staleness > self.FRESHNESS_STALE_SECS:
            freshness_bonus = -15.0  # no data at all
        elif staleness < self.FRESHNESS_FULL_SCORE_SECS:
            freshness_bonus = 15.0   # very fresh data
        elif staleness < 30:
            freshness_bonus = 10.0
        elif staleness < 60:
            freshness_bonus = 5.0
        else:
            # Linear decay between 60s and 120s
            decay_pct = (staleness - 60) / (self.FRESHNESS_STALE_SECS - 60)
            freshness_bonus = max(-15.0, 5.0 - decay_pct * 20.0)

        score += freshness_bonus
        breakdown["freshness"] = round(freshness_bonus, 2)

        # ---- Component 2: Volume spike ----
        spike_ratio = data.latest_volume_spike
        if spike_ratio >= self.VOLUME_SPIKE_THRESHOLD * 2:
            volume_bonus = 15.0    # extreme spike — someone knows something
        elif spike_ratio >= self.VOLUME_SPIKE_THRESHOLD:
            volume_bonus = 8.0     # notable spike
        elif spike_ratio >= 1.5:
            volume_bonus = 3.0
        else:
            volume_bonus = 0.0

        score += volume_bonus
        breakdown["volume_spike"] = round(volume_bonus, 2)

        # ---- Component 3: Price momentum ----
        momentum = data.price_momentum
        momentum_impact = momentum * self.MOMENTUM_SCALE  # scale for 0-1 price domain

        if abs(momentum_impact) > 0.1:
            score += momentum_impact
            if momentum > 0:
                direction = "bullish"
            elif momentum < 0:
                direction = "bearish"

        breakdown["momentum"] = round(momentum_impact, 2)

        # ---- Component 4: Consensus edge ----
        consensus_bonus = 0.0
        consensus_edge = 0.0

        if data.consensus_score is not None and current_market_price is not None:
            # Consensus score is probability from external sources (0-100)
            # Market price is the implied probability (0-1 → multiply by 100)
            market_prob = current_market_price * 100.0
            consensus_edge = data.consensus_score - market_prob

            # If sources agree on outcome with 3+ sources, larger bonus
            source_multiplier = min(data.consensus_source_count / 3.0, 2.0)

            if abs(consensus_edge) > 10:   # >10 percentage point divergence
                consensus_bonus = min(abs(consensus_edge) * 0.3 * source_multiplier, 20.0)
                if consensus_edge > 0:
                    direction = "bullish"
                else:
                    direction = "bearish"
                    consensus_bonus = -consensus_bonus  # negative means bearish, flip sign for score

                score += consensus_bonus

        breakdown["consensus_edge"] = round(consensus_edge, 2)
        breakdown["consensus_bonus"] = round(consensus_bonus, 2)

        # Clamp final score
        score = max(0.0, min(100.0, score))

        # Re-determine direction from final score
        if score > 60 and direction == "neutral":
            direction = "bullish"
        elif score < 40 and direction == "neutral":
            direction = "bearish"

        return {
            "speed_score": round(score, 2),
            "direction": direction,
            "staleness_seconds": round(staleness if staleness != float("inf") else 9999.0, 1),
            "volume_spike_ratio": round(spike_ratio, 2),
            "price_momentum": round(momentum, 5),
            "consensus_edge": round(consensus_edge, 2),
            "score_breakdown": breakdown,
        }

    def get_all_market_ids(self) -> List[str]:
        """Return all market IDs currently being tracked."""
        return list(self._data.keys())

    def clear_market(self, market_id: str) -> None:
        """Stop tracking a market (e.g., after it resolves)."""
        self._data.pop(market_id, None)
