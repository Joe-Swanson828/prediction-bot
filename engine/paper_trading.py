"""
engine/paper_trading.py — Paper trading simulation engine.

Simulates trade execution against real market data with:
  - Realistic slippage (0.1–0.5%, direction-adverse)
  - Proper prediction market P&L calculation
  - Risk management integration
  - Full DB persistence for trades, positions, and balance history

Prediction market contract pricing:
  - All prices are in [0.01, 0.99] (probability in dollars)
  - Each YES contract pays $1.00 if the outcome resolves YES
  - Each NO contract pays $1.00 if the outcome resolves NO
  - P&L = (exit_price - entry_price) * quantity_contracts

Usage:
    engine = PaperTradingEngine(starting_balance=100.0, risk=risk_manager)
    trade = await engine.execute_trade(
        market_id="kalshi:KXBTC-25DEC",
        direction="YES",
        current_price=0.52,
        composite_score={"final_score": 78, "ta_score": 72, ...}
    )
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timezone
from typing import Callable, List, Optional

from database.connection import execute_query, execute_write, get_db
from database.models import Position, Trade
from engine.risk import RiskManager


class PaperTradingEngine:
    """
    Simulates paper trades with realistic execution.

    The engine maintains in-memory state (current balance, open positions)
    that mirrors what's persisted in the database. The DB is the source
    of truth; the in-memory state is kept in sync for fast access.
    """

    # Slippage simulation range — applied direction-adversely
    SLIPPAGE_MIN: float = 0.001   # 0.1%
    SLIPPAGE_MAX: float = 0.005   # 0.5%

    # Prediction market price boundaries
    PRICE_MIN: float = 0.01
    PRICE_MAX: float = 0.99

    def __init__(
        self,
        starting_balance: float = 100.0,
        risk: Optional[RiskManager] = None,
        log_callback: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        self.balance: float = starting_balance
        self.risk: RiskManager = risk or RiskManager(starting_balance)
        self._log = log_callback or (lambda level, msg: None)
        self._mode: str = "paper"

        # Restore state from DB if any open positions exist
        self._restore_state()

    def _restore_state(self) -> None:
        """Reload open positions and balance from DB on startup."""
        try:
            # Get latest balance snapshot
            rows = execute_query(
                "SELECT balance FROM balance_history WHERE mode='paper' ORDER BY timestamp DESC LIMIT 1"
            )
            if rows:
                self.balance = rows[0]["balance"]
                self.risk.update_balance(self.balance)

            # Restore open positions into risk manager
            pos_rows = execute_query(
                """SELECT p.*, t.entry_price, t.direction, t.quantity, t.market_id
                   FROM positions p
                   JOIN trades t ON p.trade_id = t.id
                   WHERE t.status = 'open' AND t.mode = 'paper'"""
            )
            for row in pos_rows:
                cost = row["quantity"] * row["entry_price"]
                self.risk.register_position(
                    market_id=row["market_id"],
                    direction=row["direction"],
                    cost=cost,
                    entry_price=row["entry_price"],
                )
        except Exception as e:
            self._log("WARNING", f"Could not restore paper trading state: {e}")

    # ------------------------------------------------------------------ #
    # Trade execution
    # ------------------------------------------------------------------ #

    async def execute_trade(
        self,
        market_id: str,
        direction: str,
        current_price: float,
        composite_score: dict,
    ) -> Optional[Trade]:
        """
        Attempt to execute a paper trade.

        Args:
            market_id: Market identifier
            direction: 'YES' or 'NO'
            current_price: Current market price (0.0-1.0)
            composite_score: Full composite score dict from SignalAggregator

        Returns:
            Trade object if successful, None if risk check failed or invalid.
        """
        final_score = composite_score.get("final_score", 0.0)

        # Compute position size
        size = self.risk.compute_position_size(self.balance, final_score)
        if size < 0.50:
            self._log("DEBUG", f"Position size ${size:.2f} too small, skipping {market_id}")
            return None

        # Risk check
        allowed, reason = self.risk.can_trade(market_id, size)
        if not allowed:
            self._log("INFO", f"Trade rejected for {market_id}: {reason}")
            return None

        # Apply slippage (always works against the trader)
        slippage_pct = random.uniform(self.SLIPPAGE_MIN, self.SLIPPAGE_MAX)
        if direction == "YES":
            # Buying YES: we pay slightly more than the current price
            filled_price = current_price * (1.0 + slippage_pct)
        else:
            # Buying NO: we also pay slightly more for NO contracts
            filled_price = current_price * (1.0 + slippage_pct)

        # Clamp to valid prediction market price range
        filled_price = max(self.PRICE_MIN, min(self.PRICE_MAX, filled_price))

        # Compute quantity (number of contracts)
        actual_cost = min(size, self.balance)
        quantity = actual_cost / filled_price

        if quantity <= 0:
            return None

        # Deduct from balance
        self.balance -= actual_cost
        self.risk.update_balance(self.balance)

        # Build trade record
        now = datetime.now(timezone.utc).isoformat()
        signal_breakdown = {
            "ta_score": composite_score.get("ta_score", 50.0),
            "sentiment_score": composite_score.get("sentiment_score", 50.0),
            "speed_score": composite_score.get("speed_score", 50.0),
        }

        trade = Trade(
            market_id=market_id,
            exchange=self._mode,
            direction=direction,
            quantity=round(quantity, 4),
            entry_price=round(filled_price, 4),
            mode=self._mode,
            composite_score=final_score,
            signal_breakdown=signal_breakdown,
            entry_time=now,
            slippage=round(slippage_pct, 4),
            status="open",
        )

        # Persist to DB
        trade_id = self._save_trade(trade)
        trade.id = trade_id

        # Save position record
        self._save_position(trade_id, market_id, direction, quantity, filled_price)

        # Register with risk manager
        self.risk.register_position(
            market_id=market_id,
            direction=direction,
            cost=actual_cost,
            entry_price=filled_price,
        )

        # Snapshot balance
        self._save_balance_snapshot()

        self._log(
            "INFO",
            f"PAPER TRADE: {direction} {market_id} | "
            f"qty={quantity:.3f} @ ${filled_price:.4f} | "
            f"cost=${actual_cost:.2f} | score={final_score:.1f} | "
            f"slippage={slippage_pct*100:.2f}%",
        )

        return trade

    # ------------------------------------------------------------------ #
    # Position closing
    # ------------------------------------------------------------------ #

    async def close_position(
        self,
        market_id: str,
        exit_price: float,
        reason: str = "manual",
    ) -> Optional[float]:
        """
        Close an open position.

        Args:
            market_id: Market to close
            exit_price: Price at which to close (0.0-1.0)
            reason: Why the position is being closed (for logging)

        Returns:
            Realized P&L in dollars, or None if no position found.
        """
        # Get trade from DB
        rows = execute_query(
            """SELECT t.*, p.id as position_id
               FROM trades t
               JOIN positions p ON p.trade_id = t.id
               WHERE t.market_id = ? AND t.status = 'open' AND t.mode = 'paper'
               ORDER BY t.entry_time DESC LIMIT 1""",
            (market_id,),
        )
        if not rows:
            self._log("WARNING", f"No open paper position found for {market_id}")
            return None

        row = rows[0]
        trade = Trade.from_row(row)

        # Apply slippage on exit too (direction-adverse)
        slippage_pct = random.uniform(self.SLIPPAGE_MIN, self.SLIPPAGE_MAX)
        if trade.direction == "YES":
            filled_exit = exit_price * (1.0 - slippage_pct)  # sell slightly lower
        else:
            filled_exit = exit_price * (1.0 - slippage_pct)

        filled_exit = max(self.PRICE_MIN, min(self.PRICE_MAX, filled_exit))

        # Calculate P&L
        pnl = trade.calculate_pnl(filled_exit)

        # Update balance
        proceeds = filled_exit * trade.quantity
        self.balance += proceeds
        self.risk.update_balance(self.balance)

        # Update DB
        now = datetime.now(timezone.utc).isoformat()
        with get_db() as conn:
            conn.execute(
                """UPDATE trades SET status='closed', exit_price=?, exit_time=?, pnl=?
                   WHERE id=?""",
                (round(filled_exit, 4), now, round(pnl, 4), trade.id),
            )
            conn.execute("DELETE FROM positions WHERE trade_id=?", (trade.id,))

        # Remove from risk manager
        self.risk.close_position(market_id)
        self._save_balance_snapshot()

        self._log(
            "INFO",
            f"POSITION CLOSED ({reason}): {trade.direction} {market_id} | "
            f"exit=${filled_exit:.4f} | pnl=${pnl:+.2f} | "
            f"balance=${self.balance:.2f}",
        )

        return round(pnl, 4)

    async def panic_close_all(self, current_prices: dict) -> dict:
        """
        Emergency close all open positions.

        Args:
            current_prices: Dict mapping market_id -> current_price

        Returns:
            Summary dict with total_pnl and markets_closed.
        """
        open_rows = execute_query(
            """SELECT DISTINCT t.market_id FROM trades t
               WHERE t.status = 'open' AND t.mode = 'paper'"""
        )
        markets = [row["market_id"] for row in open_rows]

        total_pnl = 0.0
        closed = []
        failed = []

        for market_id in markets:
            price = current_prices.get(market_id, 0.5)
            result = await self.close_position(market_id, price, reason="panic_close")
            if result is not None:
                total_pnl += result
                closed.append(market_id)
            else:
                failed.append(market_id)

        self._log(
            "WARNING",
            f"PANIC CLOSE ALL: closed {len(closed)} positions, "
            f"total P&L=${total_pnl:+.2f}",
        )

        return {
            "markets_closed": closed,
            "markets_failed": failed,
            "total_pnl": round(total_pnl, 2),
        }

    # ------------------------------------------------------------------ #
    # Position / balance queries
    # ------------------------------------------------------------------ #

    def get_open_positions(self) -> list:
        """Return all open paper positions from the DB."""
        return execute_query(
            """SELECT p.*, t.direction, t.quantity, t.entry_price,
                      t.composite_score, t.signal_breakdown,
                      m.title, m.category, m.yes_price
               FROM positions p
               JOIN trades t ON p.trade_id = t.id
               LEFT JOIN markets m ON t.market_id = m.id
               WHERE t.status = 'open' AND t.mode = 'paper'
               ORDER BY t.entry_time DESC"""
        )

    def get_trade_history(self, limit: int = 100) -> list:
        """Return recent paper trades from the DB."""
        return execute_query(
            """SELECT t.*, m.title, m.category
               FROM trades t
               LEFT JOIN markets m ON t.market_id = m.id
               WHERE t.mode = 'paper'
               ORDER BY t.entry_time DESC
               LIMIT ?""",
            (limit,),
        )

    def get_stats(self) -> dict:
        """Return performance statistics."""
        rows = execute_query(
            """SELECT
                COUNT(*) as total_trades,
                SUM(CASE WHEN pnl > 0 THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN pnl <= 0 THEN 1 ELSE 0 END) as losses,
                SUM(pnl) as total_pnl,
                AVG(CASE WHEN pnl > 0 THEN pnl END) as avg_win,
                AVG(CASE WHEN pnl <= 0 THEN pnl END) as avg_loss,
                MAX(pnl) as best_trade,
                MIN(pnl) as worst_trade
               FROM trades
               WHERE mode = 'paper' AND status = 'closed'"""
        )
        if not rows or rows[0]["total_trades"] == 0:
            return {
                "total_trades": 0, "wins": 0, "losses": 0,
                "win_rate": 0.0, "total_pnl": 0.0,
                "avg_win": 0.0, "avg_loss": 0.0,
                "best_trade": 0.0, "worst_trade": 0.0,
                "profit_factor": 0.0,
            }

        row = rows[0]
        total = row["total_trades"] or 0
        wins = row["wins"] or 0
        win_rate = (wins / total * 100) if total > 0 else 0.0

        avg_win = row["avg_win"] or 0.0
        avg_loss = abs(row["avg_loss"] or 0.0)
        profit_factor = (avg_win / avg_loss) if avg_loss > 0 else 0.0

        return {
            "total_trades": total,
            "wins": wins,
            "losses": row["losses"] or 0,
            "win_rate": round(win_rate, 1),
            "total_pnl": round(row["total_pnl"] or 0.0, 2),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(-avg_loss, 2),
            "best_trade": round(row["best_trade"] or 0.0, 2),
            "worst_trade": round(row["worst_trade"] or 0.0, 2),
            "profit_factor": round(profit_factor, 2),
        }

    def get_today_pnl(self) -> float:
        """P&L from trades closed today."""
        rows = execute_query(
            """SELECT SUM(pnl) as today_pnl FROM trades
               WHERE mode = 'paper' AND status = 'closed'
               AND date(exit_time) = date('now')"""
        )
        if rows and rows[0]["today_pnl"] is not None:
            return round(rows[0]["today_pnl"], 2)
        return 0.0

    def get_equity_curve(self, limit: int = 100) -> list:
        """Return balance history for equity curve display."""
        rows = execute_query(
            """SELECT balance, timestamp FROM balance_history
               WHERE mode = 'paper'
               ORDER BY timestamp DESC LIMIT ?""",
            (limit,),
        )
        return list(reversed(rows)) if rows else []

    # ------------------------------------------------------------------ #
    # Internal DB helpers
    # ------------------------------------------------------------------ #

    def _save_trade(self, trade: Trade) -> int:
        """Persist a Trade to the database. Returns new row ID."""
        return execute_write(
            """INSERT INTO trades
               (market_id, exchange, direction, quantity, entry_price,
                mode, composite_score, signal_breakdown, entry_time,
                slippage, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                trade.market_id,
                trade.exchange,
                trade.direction,
                trade.quantity,
                trade.entry_price,
                trade.mode,
                trade.composite_score,
                trade.signal_breakdown_json(),
                trade.entry_time,
                trade.slippage,
                trade.status,
            ),
        )

    def _save_position(
        self,
        trade_id: int,
        market_id: str,
        direction: str,
        quantity: float,
        entry_price: float,
    ) -> int:
        """Persist a Position to the database. Returns new row ID."""
        return execute_write(
            """INSERT INTO positions
               (trade_id, market_id, direction, quantity, entry_price,
                current_price, unrealized_pnl)
               VALUES (?, ?, ?, ?, ?, ?, 0)""",
            (trade_id, market_id, direction, quantity, entry_price, entry_price),
        )

    def _save_balance_snapshot(self) -> None:
        """Save a balance snapshot to balance_history."""
        now = datetime.now(timezone.utc).isoformat()
        execute_write(
            "INSERT INTO balance_history (balance, mode, timestamp) VALUES (?, ?, ?)",
            (round(self.balance, 4), self._mode, now),
        )
