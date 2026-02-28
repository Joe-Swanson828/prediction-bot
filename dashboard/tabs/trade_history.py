"""
dashboard/tabs/trade_history.py — Tab 3: Trade history log.

Shows all closed paper trades with P&L, composite score at entry,
and signal breakdown. Includes CSV export functionality.
"""

from __future__ import annotations

import csv
import os
from datetime import datetime

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widget import Widget
from textual.widgets import Button, DataTable, Label, Static


class TradeHistoryTab(Widget):
    """Tab 3: Full trade history with P&L and signal details."""

    DEFAULT_CSS = """
    TradeHistoryTab {
        height: 1fr;
        overflow-y: auto;
    }
    .trade-controls {
        height: 3;
        padding: 0 1;
        align: left middle;
    }
    DataTable {
        height: 1fr;
    }
    .trade-totals {
        height: 2;
        padding: 0 1;
        color: $text-muted;
    }
    """

    COLUMNS = [
        ("Time", 16),
        ("Market", 28),
        ("Dir", 5),
        ("Entry $", 9),
        ("Exit $", 9),
        ("Qty", 8),
        ("P&L", 10),
        ("Score", 7),
        ("Mode", 6),
    ]

    def __init__(self, engine, **kwargs) -> None:
        super().__init__(**kwargs)
        self.engine = engine

    def compose(self) -> ComposeResult:
        with Horizontal(classes="trade-controls"):
            yield Static("Trade History (paper)", id="trade_header")
            yield Button("Export CSV", id="export_csv_btn", variant="default")
        yield DataTable(id="trades_table", zebra_stripes=True)
        yield Static("", id="trade_totals", classes="trade-totals")

    def on_mount(self) -> None:
        table = self.query_one("#trades_table", DataTable)
        for name, width in self.COLUMNS:
            table.add_column(name, width=width)
        self.set_interval(10.0, self.refresh_data)
        self.refresh_data()

    def refresh_data(self) -> None:
        """Reload trade history from the paper trading engine."""
        try:
            table = self.query_one("#trades_table", DataTable)
            table.clear()

            trades = self.engine.paper_trader.get_trade_history(limit=200)
            total_pnl = 0.0
            wins = losses = 0

            for row in trades:
                pnl = row["pnl"] if row["pnl"] is not None else None
                entry_time = (row["entry_time"] or "")[:16].replace("T", " ")

                if pnl is not None:
                    total_pnl += pnl
                    if pnl > 0:
                        wins += 1
                        pnl_str = f"[green]+${pnl:.2f}[/green]"
                    else:
                        losses += 1
                        pnl_str = f"[red]-${abs(pnl):.2f}[/red]"
                else:
                    pnl_str = "[dim]open[/dim]"

                title = (row["title"] or row["market_id"] or "")
                if len(title) > 26:
                    title = title[:25] + "…"

                exit_price = row["exit_price"]
                exit_str = f"${exit_price:.4f}" if exit_price else "—"

                table.add_row(
                    entry_time,
                    title,
                    row["direction"],
                    f"${row['entry_price']:.4f}",
                    exit_str,
                    f"{row['quantity']:.3f}",
                    pnl_str,
                    f"{row['composite_score']:.1f}" if row["composite_score"] else "—",
                    row["mode"].upper(),
                )

            # Update totals footer
            if wins + losses > 0:
                win_rate = wins / (wins + losses) * 100
                color = "green" if total_pnl >= 0 else "red"
                totals = self.query_one("#trade_totals", Static)
                totals.update(
                    f"Total: {wins+losses} trades | Wins: {wins} | "
                    f"Win Rate: {win_rate:.1f}% | "
                    f"Total P&L: [{color}]${total_pnl:+.2f}[/{color}]"
                )
        except Exception:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "export_csv_btn":
            self._export_csv()

    def _export_csv(self) -> None:
        """Export trade history to a CSV file."""
        try:
            os.makedirs("exports", exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"exports/trades_{timestamp}.csv"

            trades = self.engine.paper_trader.get_trade_history(limit=10000)
            with open(filename, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "entry_time", "exit_time", "market_id", "direction",
                    "entry_price", "exit_price", "quantity", "pnl",
                    "composite_score", "mode", "slippage",
                ])
                for row in trades:
                    writer.writerow([
                        row["entry_time"], row["exit_time"], row["market_id"],
                        row["direction"], row["entry_price"], row["exit_price"],
                        row["quantity"], row["pnl"], row["composite_score"],
                        row["mode"], row["slippage"],
                    ])

            # Show brief notification
            header = self.query_one("#trade_header", Static)
            header.update(f"Trade History — Exported to {filename}")
        except Exception as e:
            pass
