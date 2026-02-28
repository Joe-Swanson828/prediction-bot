"""
dashboard/tabs/overview.py — Tab 1: Overview dashboard.

Shows:
  - Current balance and mode (PAPER / LIVE)
  - Today's P&L and total P&L
  - Equity curve (balance history)
  - Quick stats: win rate, profit factor, best/worst trade
  - Bot status and active position count
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Label, Static


class StatCard(Static):
    """A single stat display card with label and value."""

    DEFAULT_CSS = """
    StatCard {
        border: solid $panel;
        padding: 0 1;
        height: 3;
        width: 1fr;
    }
    StatCard .stat-label {
        color: $text-muted;
        text-style: bold;
    }
    StatCard .stat-value {
        text-style: bold;
    }
    """

    def __init__(self, label: str, value: str = "--", color: str = "white", **kwargs) -> None:
        super().__init__(**kwargs)
        self._label = label
        self._value = value
        self._color = color

    def compose(self) -> ComposeResult:
        yield Label(self._label, classes="stat-label")
        yield Label(self._value, classes="stat-value", id=f"stat_value_{self.id or 'x'}")

    def update_value(self, value: str, color: str = "white") -> None:
        """Update the displayed value."""
        try:
            label = self.query_one(".stat-value", Label)
            label.update(value)
            label.styles.color = color
        except Exception:
            pass


class EquityCurve(Static):
    """Simple ASCII equity curve using block characters."""

    DEFAULT_CSS = """
    EquityCurve {
        height: 8;
        border: solid $panel;
        padding: 1;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._data: list = []

    def update_data(self, balances: list) -> None:
        """Update the chart with new balance data."""
        self._data = [float(b) for b in balances if b is not None]
        self.refresh()

    def render(self) -> str:
        if len(self._data) < 2:
            return "[dim]Equity curve — waiting for trade data...[/dim]"

        height = 5
        width = min(len(self._data), 60)
        data = self._data[-width:]

        min_val = min(data)
        max_val = max(data)
        val_range = max(max_val - min_val, 0.01)

        rows = []
        for row in range(height - 1, -1, -1):
            line = ""
            threshold = min_val + (row / (height - 1)) * val_range
            for val in data:
                if val >= threshold:
                    line += "█"
                else:
                    line += "░"
            rows.append(line)

        start = self._data[0]
        current = self._data[-1]
        change = current - start
        change_str = f"+${change:.2f}" if change >= 0 else f"-${abs(change):.2f}"
        color = "green" if change >= 0 else "red"

        chart = "\n".join(rows)
        return (
            f"[dim]Equity Curve — ${start:.2f} → ${current:.2f} "
            f"[{color}]({change_str})[/{color}][/dim]\n{chart}"
        )


class OverviewTab(Widget):
    """
    Tab 1: Main overview dashboard showing balance, mode, P&L, and stats.
    """

    DEFAULT_CSS = """
    OverviewTab {
        height: 1fr;
        overflow-y: auto;
    }
    .mode-badge {
        text-style: bold;
        padding: 0 2;
        height: 3;
        content-align: center middle;
        border: solid;
    }
    .mode-paper {
        color: $accent;
        border: solid $accent;
    }
    .mode-live {
        color: red;
        border: solid red;
    }
    .section-header {
        color: $text-muted;
        text-style: bold;
        margin: 1 0 0 0;
    }
    .pnl-positive { color: green; }
    .pnl-negative { color: red; }
    .balance-display {
        text-style: bold;
        height: 5;
        border: solid $panel;
        padding: 1;
        content-align: center middle;
    }
    """

    def __init__(self, engine, **kwargs) -> None:
        super().__init__(**kwargs)
        self.engine = engine

    def compose(self) -> ComposeResult:
        with Vertical():
            # Top row: balance and mode
            with Horizontal():
                yield Static(
                    "[$100.00]",
                    id="balance_display",
                    classes="balance-display",
                )
                yield Static(
                    "◉ PAPER MODE",
                    id="mode_badge",
                    classes="mode-badge mode-paper",
                )

            # P&L row
            with Horizontal():
                yield StatCard("Today's P&L", "$0.00", id="today_pnl")
                yield StatCard("Total P&L", "$0.00", id="total_pnl")
                yield StatCard("Open Positions", "0", id="open_positions")
                yield StatCard("Exposure", "0.0%", id="exposure_pct")

            # Equity curve
            yield Label("Equity Curve", classes="section-header")
            yield EquityCurve(id="equity_curve")

            # Quick stats row 1
            yield Label("Performance Stats", classes="section-header")
            with Horizontal():
                yield StatCard("Win Rate", "—", id="win_rate")
                yield StatCard("Profit Factor", "—", id="profit_factor")
                yield StatCard("Total Trades", "0", id="total_trades")
                yield StatCard("Avg Win", "—", id="avg_win")

            # Quick stats row 2
            with Horizontal():
                yield StatCard("Avg Loss", "—", id="avg_loss")
                yield StatCard("Best Trade", "—", id="best_trade")
                yield StatCard("Worst Trade", "—", id="worst_trade")
                yield StatCard("Uptime", "—", id="uptime")

    def on_mount(self) -> None:
        """Start refresh timer after mount."""
        self.set_interval(2.0, self.refresh_data)
        self.refresh_data()

    def refresh_data(self) -> None:
        """Pull latest data from engine and update all widgets."""
        try:
            trader = self.engine.paper_trader
            risk = self.engine.risk

            # Balance and mode
            balance = trader.balance
            mode = self.engine.config.trading_mode.upper()
            balance_display = self.query_one("#balance_display", Static)
            balance_display.update(f"[bold]${balance:.2f}[/bold]")

            mode_badge = self.query_one("#mode_badge", Static)
            if mode == "PAPER":
                mode_badge.update("◉ PAPER MODE")
                mode_badge.remove_class("mode-live")
                mode_badge.add_class("mode-paper")
            else:
                mode_badge.update("⚠ LIVE MODE")
                mode_badge.remove_class("mode-paper")
                mode_badge.add_class("mode-live")

            # P&L stats
            today_pnl = trader.get_today_pnl()
            stats = trader.get_stats()

            self._update_stat("today_pnl", f"${today_pnl:+.2f}", "green" if today_pnl >= 0 else "red")
            self._update_stat("total_pnl", f"${stats['total_pnl']:+.2f}", "green" if stats['total_pnl'] >= 0 else "red")
            self._update_stat("open_positions", str(risk.position_count))
            self._update_stat("exposure_pct", f"{risk.exposure_pct*100:.1f}%")

            # Performance stats
            if stats["total_trades"] > 0:
                self._update_stat("win_rate", f"{stats['win_rate']:.1f}%")
                self._update_stat("profit_factor", f"{stats['profit_factor']:.2f}x")
                self._update_stat("total_trades", str(stats["total_trades"]))
                self._update_stat("avg_win", f"${stats['avg_win']:.2f}")
                self._update_stat("avg_loss", f"${stats['avg_loss']:.2f}")
                self._update_stat("best_trade", f"${stats['best_trade']:.2f}")
                self._update_stat("worst_trade", f"${stats['worst_trade']:.2f}")

            # Equity curve
            equity_data = trader.get_equity_curve(limit=60)
            if equity_data:
                balances = [row["balance"] for row in equity_data]
                self.query_one("#equity_curve", EquityCurve).update_data(balances)

        except Exception:
            pass  # never crash the dashboard on data refresh

    def _update_stat(self, widget_id: str, value: str, color: str = "white") -> None:
        """Safely update a StatCard value."""
        try:
            card = self.query_one(f"#{widget_id}", StatCard)
            card.update_value(value, color)
        except Exception:
            pass
