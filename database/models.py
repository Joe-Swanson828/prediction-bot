"""
database/models.py — Python dataclasses mirroring each database table.

These classes are used for type-safe row handling throughout the codebase.
They mirror the database schema defined in schema.py.

Each model has:
  - A from_row() classmethod to build from a sqlite3.Row
  - An as_tuple() method for INSERT statements
  - Type-safe fields matching the DB column types
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Market:
    """Represents a row in the `markets` table."""

    id: str                        # "kalshi:TICKER" or "polymarket:TOKEN_ID"
    exchange: str                  # 'kalshi' | 'polymarket'
    ticker: str
    category: str                  # 'sports' | 'crypto' | 'weather'
    title: str
    yes_price: float = 0.5
    no_price: float = 0.5
    volume: float = 0.0
    open_interest: float = 0.0
    close_date: Optional[str] = None
    status: str = "active"
    last_updated: Optional[str] = None

    @classmethod
    def from_row(cls, row) -> "Market":
        """Build from a sqlite3.Row or dict-like object."""
        return cls(
            id=row["id"],
            exchange=row["exchange"],
            ticker=row["ticker"],
            category=row["category"],
            title=row["title"],
            yes_price=row["yes_price"] or 0.5,
            no_price=row["no_price"] or 0.5,
            volume=row["volume"] or 0.0,
            open_interest=row["open_interest"] or 0.0,
            close_date=row["close_date"],
            status=row["status"] or "active",
            last_updated=row["last_updated"],
        )

    @property
    def display_name(self) -> str:
        """Short display name for dashboard tables."""
        return self.title[:50] if len(self.title) > 50 else self.title


@dataclass
class Candlestick:
    """Represents a row in the `candlesticks` table."""

    market_id: str
    timestamp: str
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    period_min: int = 1
    id: Optional[int] = None

    @classmethod
    def from_row(cls, row) -> "Candlestick":
        return cls(
            id=row["id"],
            market_id=row["market_id"],
            timestamp=row["timestamp"],
            open=row["open"] or 0.0,
            high=row["high"] or 0.0,
            low=row["low"] or 0.0,
            close=row["close"] or 0.0,
            volume=row["volume"] or 0.0,
            period_min=row["period_min"] or 1,
        )

    def as_dict(self) -> dict:
        """Dictionary form used by the TA engine."""
        return {
            "timestamp": self.timestamp,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }


@dataclass
class Signal:
    """Represents a row in the `signals` table."""

    market_id: str
    signal_type: str               # 'ta' | 'sentiment' | 'speed'
    signal_name: str
    value: float                   # score 0-100
    direction: str = "neutral"     # 'bullish' | 'bearish' | 'neutral'
    confidence: float = 0.0
    metadata: Optional[dict] = None
    acted_on: bool = False
    timestamp: Optional[str] = None
    id: Optional[int] = None

    @classmethod
    def from_row(cls, row) -> "Signal":
        meta = None
        if row["metadata"]:
            try:
                meta = json.loads(row["metadata"])
            except (json.JSONDecodeError, TypeError):
                meta = None
        return cls(
            id=row["id"],
            market_id=row["market_id"],
            signal_type=row["signal_type"],
            signal_name=row["signal_name"],
            value=row["value"] or 0.0,
            direction=row["direction"] or "neutral",
            confidence=row["confidence"] or 0.0,
            metadata=meta,
            acted_on=bool(row["acted_on"]),
            timestamp=row["timestamp"],
        )

    def metadata_json(self) -> str:
        """Serialize metadata dict to JSON string for DB storage."""
        if self.metadata is None:
            return "{}"
        return json.dumps(self.metadata)


@dataclass
class CompositeScore:
    """Represents a row in the `composite_scores` table."""

    market_id: str
    ta_score: float
    sentiment_score: float
    speed_score: float
    ta_weight: float
    sentiment_weight: float
    speed_weight: float
    final_score: float
    recommendation: str = "HOLD"   # 'BUY_YES' | 'BUY_NO' | 'HOLD'
    timestamp: Optional[str] = None
    id: Optional[int] = None

    @classmethod
    def from_row(cls, row) -> "CompositeScore":
        return cls(
            id=row["id"],
            market_id=row["market_id"],
            ta_score=row["ta_score"] or 0.0,
            sentiment_score=row["sentiment_score"] or 0.0,
            speed_score=row["speed_score"] or 0.0,
            ta_weight=row["ta_weight"] or 0.33,
            sentiment_weight=row["sentiment_weight"] or 0.33,
            speed_weight=row["speed_weight"] or 0.34,
            final_score=row["final_score"] or 0.0,
            recommendation=row["recommendation"] or "HOLD",
            timestamp=row["timestamp"],
        )


@dataclass
class Trade:
    """Represents a row in the `trades` table."""

    market_id: str
    exchange: str
    direction: str                 # 'YES' | 'NO'
    quantity: float                # number of contracts
    entry_price: float             # price per contract at entry
    mode: str                      # 'paper' | 'live'
    composite_score: float = 0.0
    signal_breakdown: dict = field(default_factory=dict)
    entry_time: Optional[str] = None
    exit_price: Optional[float] = None
    exit_time: Optional[str] = None
    pnl: Optional[float] = None
    status: str = "open"           # 'open' | 'closed' | 'cancelled'
    slippage: float = 0.0
    id: Optional[int] = None

    @classmethod
    def from_row(cls, row) -> "Trade":
        breakdown = {}
        if row["signal_breakdown"]:
            try:
                breakdown = json.loads(row["signal_breakdown"])
            except (json.JSONDecodeError, TypeError):
                breakdown = {}
        return cls(
            id=row["id"],
            market_id=row["market_id"],
            exchange=row["exchange"],
            direction=row["direction"],
            quantity=row["quantity"] or 0.0,
            entry_price=row["entry_price"] or 0.0,
            exit_price=row["exit_price"],
            entry_time=row["entry_time"],
            exit_time=row["exit_time"],
            pnl=row["pnl"],
            status=row["status"] or "open",
            composite_score=row["composite_score"] or 0.0,
            signal_breakdown=breakdown,
            slippage=row["slippage"] or 0.0,
            mode=row["mode"],
        )

    def signal_breakdown_json(self) -> str:
        """Serialize signal breakdown for DB storage."""
        return json.dumps(self.signal_breakdown)

    @property
    def cost(self) -> float:
        """Total cost of this trade in dollars."""
        return self.quantity * self.entry_price

    def calculate_pnl(self, exit_price: float) -> float:
        """
        Calculate realized P&L for this trade at a given exit price.

        For YES contracts: profit when price rises (bought cheap, sold higher)
        For NO contracts: profit when price falls
        Each contract pays $1.00 on resolution win.

        P&L = (exit_price - entry_price) * quantity
        """
        return (exit_price - self.entry_price) * self.quantity


@dataclass
class Position:
    """Represents a row in the `positions` table."""

    trade_id: int
    market_id: str
    direction: str
    quantity: float
    entry_price: float
    current_price: Optional[float] = None
    unrealized_pnl: float = 0.0
    last_updated: Optional[str] = None
    id: Optional[int] = None

    @classmethod
    def from_row(cls, row) -> "Position":
        return cls(
            id=row["id"],
            trade_id=row["trade_id"],
            market_id=row["market_id"],
            direction=row["direction"],
            quantity=row["quantity"] or 0.0,
            entry_price=row["entry_price"] or 0.0,
            current_price=row["current_price"],
            unrealized_pnl=row["unrealized_pnl"] or 0.0,
            last_updated=row["last_updated"],
        )

    def update_unrealized_pnl(self, current_price: float) -> float:
        """Recalculate and store unrealized P&L at the current price."""
        self.current_price = current_price
        self.unrealized_pnl = (current_price - self.entry_price) * self.quantity
        return self.unrealized_pnl

    @property
    def cost(self) -> float:
        """Total cost basis of this position."""
        return self.quantity * self.entry_price


@dataclass
class BalanceSnapshot:
    """Represents a row in the `balance_history` table."""

    balance: float
    mode: str                      # 'paper' | 'live'
    timestamp: Optional[str] = None
    id: Optional[int] = None

    @classmethod
    def from_row(cls, row) -> "BalanceSnapshot":
        return cls(
            id=row["id"],
            balance=row["balance"] or 0.0,
            mode=row["mode"],
            timestamp=row["timestamp"],
        )


@dataclass
class AgentLogEntry:
    """Represents a row in the `agent_log` table."""

    action: str
    category: Optional[str] = None
    old_value: Optional[dict] = None
    new_value: Optional[dict] = None
    reason: Optional[str] = None
    timestamp: Optional[str] = None
    id: Optional[int] = None

    @classmethod
    def from_row(cls, row) -> "AgentLogEntry":
        old_val = None
        new_val = None
        try:
            if row["old_value"]:
                old_val = json.loads(row["old_value"])
            if row["new_value"]:
                new_val = json.loads(row["new_value"])
        except (json.JSONDecodeError, TypeError):
            pass
        return cls(
            id=row["id"],
            action=row["action"],
            category=row["category"],
            old_value=old_val,
            new_value=new_val,
            reason=row["reason"],
            timestamp=row["timestamp"],
        )


@dataclass
class DataSourceStatus:
    """Represents a row in the `data_source_status` table."""

    id: str                        # e.g. 'kalshi_rest', 'openweather'
    source_name: str
    status: str = "unknown"        # 'healthy' | 'degraded' | 'down' | 'unknown'
    last_success: Optional[str] = None
    last_error: Optional[str] = None
    error_count: int = 0
    latency_ms: Optional[float] = None

    @classmethod
    def from_row(cls, row) -> "DataSourceStatus":
        return cls(
            id=row["id"],
            source_name=row["source_name"],
            status=row["status"] or "unknown",
            last_success=row["last_success"],
            last_error=row["last_error"],
            error_count=row["error_count"] or 0,
            latency_ms=row["latency_ms"],
        )

    @property
    def status_icon(self) -> str:
        """Terminal-friendly status indicator."""
        return {"healthy": "✓", "degraded": "⚠", "down": "✗", "unknown": "?"}.get(
            self.status, "?"
        )


@dataclass
class BotLogEntry:
    """Represents a row in the `bot_log` table."""

    level: str
    message: str
    module: Optional[str] = None
    timestamp: Optional[str] = None
    id: Optional[int] = None

    @classmethod
    def from_row(cls, row) -> "BotLogEntry":
        return cls(
            id=row["id"],
            level=row["level"] or "INFO",
            module=row["module"],
            message=row["message"],
            timestamp=row["timestamp"],
        )


@dataclass
class StrategyWeights:
    """Represents a row in the `strategy_weights` table."""

    category: str
    ta_weight: float
    sentiment_weight: float
    speed_weight: float
    performance_score: Optional[float] = None
    updated_at: Optional[str] = None
    id: Optional[int] = None

    @classmethod
    def from_row(cls, row) -> "StrategyWeights":
        return cls(
            id=row["id"],
            category=row["category"],
            ta_weight=row["ta_weight"] or 0.33,
            sentiment_weight=row["sentiment_weight"] or 0.33,
            speed_weight=row["speed_weight"] or 0.34,
            performance_score=row["performance_score"],
            updated_at=row["updated_at"],
        )

    def as_dict(self) -> dict:
        return {
            "ta": self.ta_weight,
            "sentiment": self.sentiment_weight,
            "speed": self.speed_weight,
        }
