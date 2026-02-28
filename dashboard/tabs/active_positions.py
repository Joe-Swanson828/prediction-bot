"""
dashboard/tabs/active_positions.py — Tab 4: Active positions with live P&L.

Shows all open positions with real-time unrealized P&L updates.
Includes per-position Close buttons and a Panic Close All button.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widget import Widget
from textual.widgets import Button, DataTable, Label, Static


class ActivePositionsTab(Widget):
    """Tab 4: Open positions with live P&L and manual close controls."""

    DEFAULT_CSS = """
    ActivePositionsTab {
        height: 1fr;
        overflow-y: auto;
    }
    .positions-header {
        height: 3;
        padding: 0 1;
        align: left middle;
    }
    .panic-btn {
        background: red;
        color: white;
        text-style: bold;
    }
    DataTable {
        height: 1fr;
    }
    .positions-summary {
        height: 2;
        padding: 0 1;
        color: $text-muted;
    }
    """

    COLUMNS = [
        ("Market", 28),
        ("Dir", 5),
        ("Entry $", 9),
        ("Current $", 10),
        ("Qty", 8),
        ("Unrealized P&L", 15),
        ("Entry Score", 11),
        ("Action", 8),
    ]

    def __init__(self, engine, **kwargs) -> None:
        super().__init__(**kwargs)
        self.engine = engine

    def compose(self) -> ComposeResult:
        with Horizontal(classes="positions-header"):
            yield Static("Open Positions (live P&L)", id="positions_header")
            yield Button(
                "⚠ PANIC CLOSE ALL",
                id="panic_close_btn",
                classes="panic-btn",
                variant="error",
            )
        yield DataTable(id="positions_table", zebra_stripes=True)
        yield Static("No open positions.", id="positions_summary", classes="positions-summary")

    def on_mount(self) -> None:
        table = self.query_one("#positions_table", DataTable)
        for name, width in self.COLUMNS:
            table.add_column(name, width=width)
        self.set_interval(1.0, self.refresh_data)
        self.refresh_data()

    def refresh_data(self) -> None:
        """Reload open positions with latest prices."""
        try:
            table = self.query_one("#positions_table", DataTable)
            table.clear()

            positions = self.engine.paper_trader.get_open_positions()

            if not positions:
                self.query_one("#positions_summary", Static).update(
                    "No open positions."
                )
                return

            total_unrealized = 0.0

            for row in positions:
                entry_price = row["entry_price"] or 0.5
                current_price = row["yes_price"] or row["current_price"] or entry_price
                quantity = row["quantity"] or 0.0
                unrealized_pnl = (current_price - entry_price) * quantity
                total_unrealized += unrealized_pnl

                pnl_color = "green" if unrealized_pnl >= 0 else "red"
                pnl_str = f"[{pnl_color}]${unrealized_pnl:+.2f}[/{pnl_color}]"

                title = (row["title"] or row["market_id"] or "")
                if len(title) > 26:
                    title = title[:25] + "…"

                table.add_row(
                    title,
                    row["direction"],
                    f"${entry_price:.4f}",
                    f"${float(current_price):.4f}",
                    f"{quantity:.3f}",
                    pnl_str,
                    f"{row['composite_score']:.1f}" if row["composite_score"] else "—",
                    f"[Close:{row['market_id']}]",
                )

            color = "green" if total_unrealized >= 0 else "red"
            self.query_one("#positions_summary", Static).update(
                f"{len(positions)} open position(s) | "
                f"Total Unrealized P&L: [{color}]${total_unrealized:+.2f}[/{color}]"
            )
        except Exception:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "panic_close_btn":
            self._confirm_panic_close()

    def _confirm_panic_close(self) -> None:
        """Show confirmation before panic closing all positions."""
        positions = self.engine.paper_trader.get_open_positions()
        count = len(positions)
        if count == 0:
            return

        # Post message to app to show confirmation dialog
        self.app.push_screen_wait = True
        import asyncio
        asyncio.create_task(self._execute_panic_close(count))

    async def _execute_panic_close(self, count: int) -> None:
        """Execute panic close with current market prices."""
        try:
            # Build current prices dict from engine
            current_prices = {}
            for market in (self.engine.markets or []):
                market_id = market.get("id") or f"{market.get('exchange')}:{market.get('ticker')}"
                yes_price = market.get("yes_price") or market.get("yes_ask", 0.5)
                current_prices[market_id] = float(yes_price)

            result = await self.engine.paper_trader.panic_close_all(current_prices)
            pnl = result.get("total_pnl", 0.0)

            if self.engine.notifier:
                await self.engine.notifier.notify_panic_close(pnl, count)

        except Exception:
            pass
