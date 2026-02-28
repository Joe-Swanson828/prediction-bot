"""
dashboard/tabs/signal_log.py — Tab 5: Signal detection log.

Shows all signals detected by all three analysis engines:
  TA signals = blue
  Sentiment signals = magenta/purple
  Speed signals = yellow/orange

Supports filtering by signal type.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widget import Widget
from textual.widgets import Button, RichLog, Static


class SignalLogTab(Widget):
    """Tab 5: Real-time signal log from all three signal types."""

    DEFAULT_CSS = """
    SignalLogTab {
        height: 1fr;
    }
    .signal-controls {
        height: 3;
        padding: 0 1;
        align: left middle;
    }
    RichLog {
        height: 1fr;
        border: solid $panel;
    }
    """

    # Color mapping per signal type
    SIGNAL_COLORS = {
        "ta": "blue",
        "sentiment": "magenta",
        "speed": "yellow",
        "composite": "green",
    }

    def __init__(self, engine, **kwargs) -> None:
        super().__init__(**kwargs)
        self.engine = engine
        self._filter: str = "all"
        self._last_signal_id: int = 0

    def compose(self) -> ComposeResult:
        with Horizontal(classes="signal-controls"):
            yield Static("Signal Log", id="signal_log_header")
            yield Button("All", id="filter_all", variant="primary")
            yield Button("TA", id="filter_ta", variant="default")
            yield Button("Sentiment", id="filter_sentiment", variant="default")
            yield Button("Speed", id="filter_speed", variant="default")
        yield RichLog(id="signal_richlog", markup=True, highlight=True, wrap=True)

    def on_mount(self) -> None:
        self.set_interval(3.0, self.refresh_data)
        self.refresh_data()

    def refresh_data(self) -> None:
        """Load new signals from DB since last check."""
        try:
            from database.connection import execute_query

            # Only load signals newer than last seen
            if self._filter == "all":
                rows = execute_query(
                    """SELECT * FROM signals WHERE id > ?
                       ORDER BY timestamp ASC LIMIT 50""",
                    (self._last_signal_id,),
                )
            else:
                rows = execute_query(
                    """SELECT * FROM signals WHERE id > ? AND signal_type = ?
                       ORDER BY timestamp ASC LIMIT 50""",
                    (self._last_signal_id, self._filter),
                )

            if not rows:
                return

            log = self.query_one("#signal_richlog", RichLog)
            for row in rows:
                self._last_signal_id = max(self._last_signal_id, row["id"])
                self._add_signal_entry(log, row)

        except Exception:
            pass

    def _add_signal_entry(self, log: RichLog, row) -> None:
        """Format and add a signal entry to the log."""
        signal_type = row["signal_type"] or "ta"
        color = self.SIGNAL_COLORS.get(signal_type, "white")
        ts = (row["timestamp"] or "")[:19].replace("T", " ")
        direction = row["direction"] or "neutral"
        value = row["value"] or 0.0
        acted = " [executed]" if row["acted_on"] else ""

        # Format the log line
        market_id = row["market_id"] or ""
        market_short = market_id.split(":")[-1] if ":" in market_id else market_id
        if len(market_short) > 20:
            market_short = market_short[:19] + "…"

        log.write(
            f"[dim]{ts}[/dim] "
            f"[{color}][{signal_type.upper():9}][/{color}] "
            f"[bold]{market_short:20}[/bold] "
            f"score=[{color}]{value:.1f}[/{color}] "
            f"direction={direction}{acted}"
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        filter_map = {
            "filter_all": "all",
            "filter_ta": "ta",
            "filter_sentiment": "sentiment",
            "filter_speed": "speed",
        }
        if btn_id in filter_map:
            self._filter = filter_map[btn_id]
            self._last_signal_id = 0
            log = self.query_one("#signal_richlog", RichLog)
            log.clear()
            self.refresh_data()
