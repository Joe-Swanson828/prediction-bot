"""
engine/risk.py — Risk management for the prediction market trading bot.

Enforces position limits, exposure constraints, and Kelly-inspired sizing.
All monetary values are in USD. All fractions are in [0, 1].

Rules enforced:
  - Max concurrent positions: configurable (default 5)
  - Max exposure per trade: configurable fraction of balance (default 20%)
  - Max total exposure: configurable fraction of balance (default 80%)
  - No duplicate positions in the same market
  - Position sizing scales with composite score confidence
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from config import config


@dataclass
class OpenPosition:
    """Lightweight representation of an open position for risk tracking."""

    market_id: str
    direction: str          # 'YES' | 'NO'
    cost: float             # dollars committed
    entry_price: float


class RiskManager:
    """
    Manages position limits and sizes for paper and live trading.

    Usage:
        risk = RiskManager(starting_balance=100.0)
        allowed, reason = risk.can_trade("kalshi:KXBTC-25DEC", proposed_size=10.0)
        if allowed:
            size = risk.compute_position_size(balance=100.0, score=78.0)
            risk.register_position("kalshi:KXBTC-25DEC", "YES", size, 0.52)
    """

    def __init__(self, starting_balance: float = 100.0) -> None:
        self._current_balance: float = starting_balance
        self._open_positions: Dict[str, OpenPosition] = {}

        # Load limits from config (can be overridden at runtime)
        self.max_positions: int = config.max_positions
        self.max_exposure_per_trade: float = config.max_exposure_per_trade
        self.max_total_exposure: float = config.max_total_exposure
        self.trade_threshold: int = config.trade_threshold

    # ------------------------------------------------------------------ #
    # Core guard — called before every trade attempt
    # ------------------------------------------------------------------ #

    def can_trade(
        self,
        market_id: str,
        proposed_size: float,
    ) -> tuple[bool, str]:
        """
        Check whether a trade is permissible under current risk rules.

        Args:
            market_id: Unique market identifier
            proposed_size: Dollar amount proposed for this trade

        Returns:
            (allowed: bool, reason: str)
        """
        # Rule 1: Max concurrent positions
        if len(self._open_positions) >= self.max_positions:
            return False, (
                f"Max positions reached ({self.max_positions}). "
                f"Close a position before opening another."
            )

        # Rule 2: No duplicate positions in same market
        if market_id in self._open_positions:
            return False, f"Already have an open position in {market_id}."

        # Rule 3: Per-trade exposure limit
        max_single = self._current_balance * self.max_exposure_per_trade
        if proposed_size > max_single:
            return False, (
                f"Proposed size ${proposed_size:.2f} exceeds per-trade limit "
                f"${max_single:.2f} ({self.max_exposure_per_trade*100:.0f}% of balance)."
            )

        # Rule 4: Total exposure limit
        current_exposure = self.total_exposure
        max_total = self._current_balance * self.max_total_exposure
        if current_exposure + proposed_size > max_total:
            return False, (
                f"Would exceed total exposure limit. "
                f"Current: ${current_exposure:.2f}, "
                f"Max: ${max_total:.2f} ({self.max_total_exposure*100:.0f}% of balance)."
            )

        # Rule 5: Minimum balance check
        if proposed_size > self._current_balance:
            return False, (
                f"Insufficient balance. "
                f"Proposed ${proposed_size:.2f} > available ${self._current_balance:.2f}."
            )

        return True, "OK"

    # ------------------------------------------------------------------ #
    # Position sizing
    # ------------------------------------------------------------------ #

    def compute_position_size(self, balance: float, score: float) -> float:
        """
        Kelly-inspired position sizing based on composite confidence score.

        Scales linearly from minimum (5% of balance at threshold score)
        to maximum (20% of balance at perfect score 100):

            size_pct = 0.05 + (score - threshold) / (100 - threshold) * 0.15

        For a $100 balance with threshold=65:
          score=65  → 5%  = $5.00
          score=75  → 9.3% = $9.30
          score=85  → 13.6% = $13.60
          score=100 → 20%  = $20.00

        Args:
            balance: Current available balance in dollars
            score: Composite score 0-100

        Returns:
            Dollar size for this position
        """
        threshold = self.trade_threshold
        score_range = 100 - threshold  # the range we care about

        if score <= threshold:
            size_pct = 0.05
        else:
            normalized = (score - threshold) / score_range
            size_pct = 0.05 + normalized * 0.15

        # Cap at max exposure per trade
        size_pct = min(size_pct, self.max_exposure_per_trade)

        return round(balance * size_pct, 2)

    # ------------------------------------------------------------------ #
    # Position lifecycle
    # ------------------------------------------------------------------ #

    def register_position(
        self,
        market_id: str,
        direction: str,
        cost: float,
        entry_price: float,
    ) -> None:
        """
        Register an open position. Call after a successful trade execution.
        """
        self._open_positions[market_id] = OpenPosition(
            market_id=market_id,
            direction=direction,
            cost=cost,
            entry_price=entry_price,
        )

    def close_position(self, market_id: str) -> Optional[OpenPosition]:
        """
        Remove an open position from tracking. Returns the closed position.
        Returns None if the market wasn't being tracked.
        """
        return self._open_positions.pop(market_id, None)

    def update_balance(self, new_balance: float) -> None:
        """Update the tracked balance (e.g., after a trade is closed)."""
        self._current_balance = new_balance

    # ------------------------------------------------------------------ #
    # Queries / properties
    # ------------------------------------------------------------------ #

    @property
    def current_balance(self) -> float:
        """Current tracked balance."""
        return self._current_balance

    @property
    def total_exposure(self) -> float:
        """Total dollars currently committed to open positions."""
        return sum(p.cost for p in self._open_positions.values())

    @property
    def available_balance(self) -> float:
        """Balance minus committed exposure."""
        return max(0.0, self._current_balance - self.total_exposure)

    @property
    def exposure_pct(self) -> float:
        """Current exposure as a fraction of balance."""
        if self._current_balance <= 0:
            return 0.0
        return self.total_exposure / self._current_balance

    @property
    def position_count(self) -> int:
        """Number of currently open positions."""
        return len(self._open_positions)

    def get_position(self, market_id: str) -> Optional[OpenPosition]:
        """Return tracking data for a specific position, or None."""
        return self._open_positions.get(market_id)

    def get_all_positions(self) -> List[OpenPosition]:
        """Return all open positions."""
        return list(self._open_positions.values())

    def summary(self) -> dict:
        """Return a summary dict suitable for dashboard display."""
        return {
            "balance": round(self._current_balance, 2),
            "total_exposure": round(self.total_exposure, 2),
            "available_balance": round(self.available_balance, 2),
            "exposure_pct": round(self.exposure_pct * 100, 1),
            "position_count": self.position_count,
            "max_positions": self.max_positions,
        }
