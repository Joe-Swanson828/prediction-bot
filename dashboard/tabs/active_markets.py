"""
dashboard/tabs/active_markets.py — Tab 2: Active markets scanner.

Displays all prediction markets being monitored with:
  - Current YES price
  - Category and exchange
  - Composite score (color-coded)
  - Breakout state from TA engine
  - Refresh every 5 seconds
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import DataTable, Label, Static


class ActiveMarketsTab(Widget):
    """Tab 2: Shows all monitored markets with signal scores."""

    DEFAULT_CSS = """
    ActiveMarketsTab {
        height: 1fr;
        overflow-y: auto;
    }
    .markets-header {
        height: 2;
        padding: 0 1;
        color: $text-muted;
    }
    DataTable {
        height: 1fr;
    }
    """

    COLUMNS = [
        ("Title", 35),
        ("Exchange", 10),
        ("Category", 8),
        ("YES Price", 10),
        ("Score", 7),
        ("Direction", 10),
        ("TA State", 22),
        ("Volume", 8),
    ]

    def __init__(self, engine, **kwargs) -> None:
        super().__init__(**kwargs)
        self.engine = engine

    def compose(self) -> ComposeResult:
        yield Static(
            "Active Markets — Score ≥ 65 highlighted in green",
            classes="markets-header",
        )
        yield DataTable(id="markets_table", zebra_stripes=True)

    def on_mount(self) -> None:
        table = self.query_one("#markets_table", DataTable)
        for name, width in self.COLUMNS:
            table.add_column(name, width=width)
        self.set_interval(5.0, self.refresh_data)
        self.refresh_data()

    def refresh_data(self) -> None:
        """Reload market data from engine."""
        try:
            table = self.query_one("#markets_table", DataTable)
            table.clear()

            markets = getattr(self.engine, "markets", [])
            scores = getattr(self.engine, "latest_scores", {})

            for market in markets:
                market_id = market.get("id") or f"{market.get('exchange')}:{market.get('ticker')}"
                score_dict = scores.get(market_id, {})
                final_score = score_dict.get("final_score", 0.0)
                direction = score_dict.get("direction", "—")
                ta_state = score_dict.get("ta_breakout_state", "SCANNING")

                yes_price = market.get("yes_price") or market.get("yes_ask", 0.5)
                volume = market.get("volume", 0)

                # Color code by score
                if final_score >= 65:
                    score_str = f"[green]{final_score:.1f}[/green]"
                elif final_score >= 50:
                    score_str = f"[yellow]{final_score:.1f}[/yellow]"
                else:
                    score_str = f"[dim]{final_score:.1f}[/dim]"

                direction_str = {
                    "bullish": "[green]↑ Bullish[/green]",
                    "bearish": "[red]↓ Bearish[/red]",
                }.get(direction, "[dim]Neutral[/dim]")

                title = market.get("title", market_id)
                if len(title) > 33:
                    title = title[:32] + "…"

                table.add_row(
                    title,
                    market.get("exchange", "—").capitalize(),
                    market.get("category", "—").capitalize(),
                    f"${float(yes_price):.3f}",
                    score_str,
                    direction_str,
                    ta_state,
                    f"{int(volume):,}",
                )
        except Exception:
            pass
