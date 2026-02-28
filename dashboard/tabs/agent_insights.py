"""
dashboard/tabs/agent_insights.py — Tab 7: Agent performance and weight history.

Shows:
  - Current signal weights per category
  - Agent adjustment history with reasoning
  - Signal accuracy per type
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widget import Widget
from textual.widgets import DataTable, Label, Static


class AgentInsightsTab(Widget):
    """Tab 7: Agent weight history and signal performance metrics."""

    DEFAULT_CSS = """
    AgentInsightsTab {
        height: 1fr;
        overflow-y: auto;
    }
    .section-title {
        color: $text-muted;
        text-style: bold;
        margin: 1 0 0 1;
    }
    .weight-display {
        height: 3;
        border: solid $panel;
        padding: 0 1;
        width: 1fr;
    }
    DataTable {
        height: 12;
    }
    """

    def __init__(self, engine, **kwargs) -> None:
        super().__init__(**kwargs)
        self.engine = engine

    def compose(self) -> ComposeResult:
        yield Label("Current Signal Weights", classes="section-title")
        with Horizontal():
            yield Static("Sports: TA=0.20 Sent=0.35 Speed=0.45", id="weights_sports", classes="weight-display")
            yield Static("Crypto: TA=0.40 Sent=0.30 Speed=0.30", id="weights_crypto", classes="weight-display")
            yield Static("Weather: TA=0.15 Sent=0.05 Speed=0.80", id="weights_weather", classes="weight-display")

        yield Label("Agent Adjustment History", classes="section-title")
        yield DataTable(id="agent_log_table", zebra_stripes=True)

    def on_mount(self) -> None:
        table = self.query_one("#agent_log_table", DataTable)
        table.add_column("Time", width=18)
        table.add_column("Category", width=10)
        table.add_column("Action", width=18)
        table.add_column("Old Weights", width=30)
        table.add_column("New Weights", width=30)
        table.add_column("Reason", width=50)
        self.set_interval(30.0, self.refresh_data)
        self.refresh_data()

    def refresh_data(self) -> None:
        """Reload weight data and agent log from DB."""
        try:
            from database.schema import get_current_weights

            for category in ("sports", "crypto", "weather"):
                weights = get_current_weights(category)
                ta = weights.get("ta", 0.0)
                sent = weights.get("sentiment", 0.0)
                speed = weights.get("speed", 0.0)
                widget = self.query_one(f"#weights_{category}", Static)
                widget.update(
                    f"[bold]{category.capitalize()}[/bold]  "
                    f"[blue]TA={ta:.2f}[/blue]  "
                    f"[magenta]Sent={sent:.2f}[/magenta]  "
                    f"[yellow]Speed={speed:.2f}[/yellow]"
                )

            # Agent log
            table = self.query_one("#agent_log_table", DataTable)
            table.clear()

            rows = self.engine.agent.get_adjustment_history(limit=50)
            for row in rows:
                ts = (row["timestamp"] or "")[:19].replace("T", " ")
                old_val = row["old_value"] or ""
                new_val = row["new_value"] or ""
                reason = (row["reason"] or "")[:48]

                table.add_row(
                    ts,
                    row["category"] or "—",
                    row["action"] or "—",
                    old_val[:28],
                    new_val[:28],
                    reason,
                )
        except Exception:
            pass
