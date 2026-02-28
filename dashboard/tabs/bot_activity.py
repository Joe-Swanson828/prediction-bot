"""
dashboard/tabs/bot_activity.py — Tab 9: Real-time bot activity log.

Shows a scrolling, color-coded log of everything the bot is doing.

Color coding:
  DEBUG   = gray/dim
  INFO    = white
  WARNING = yellow
  ERROR   = red

Signal-related log entries get additional color:
  TA signals      = blue
  Sentiment       = magenta
  Speed signals   = yellow/orange
  Trades          = green/red
  Agent actions   = cyan
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widget import Widget
from textual.widgets import Button, RichLog, Static


class BotActivityTab(Widget):
    """Tab 9: Scrolling real-time bot activity log."""

    DEFAULT_CSS = """
    BotActivityTab {
        height: 1fr;
    }
    .log-controls {
        height: 3;
        padding: 0 1;
        align: left middle;
    }
    RichLog {
        height: 1fr;
        border: solid $panel;
    }
    """

    # Level → Rich markup color
    LEVEL_COLORS = {
        "DEBUG": "dim",
        "INFO": "white",
        "WARNING": "yellow",
        "ERROR": "red bold",
    }

    # Message keywords → highlight colors
    KEYWORD_COLORS = {
        "TRADE": "green bold",
        "PAPER TRADE": "green bold",
        "POSITION CLOSED": "cyan",
        "AGENT": "cyan",
        "SIGNAL": "blue",
        "ERROR": "red bold",
        "PANIC": "red bold blink",
    }

    def __init__(self, engine, **kwargs) -> None:
        super().__init__(**kwargs)
        self.engine = engine
        self._last_log_id: int = 0
        self._auto_scroll: bool = True
        self._filter: str = "all"

    def compose(self) -> ComposeResult:
        with Horizontal(classes="log-controls"):
            yield Static("Bot Activity Log", id="activity_header")
            yield Button("All", id="log_filter_all", variant="primary")
            yield Button("INFO+", id="log_filter_info", variant="default")
            yield Button("WARN+", id="log_filter_warn", variant="default")
            yield Button("Pause Scroll", id="toggle_scroll_btn", variant="default")
            yield Button("Clear", id="clear_log_btn", variant="default")
        yield RichLog(id="activity_log", markup=True, highlight=True, wrap=True)

    def on_mount(self) -> None:
        self.set_interval(1.0, self.refresh_data)

    def refresh_data(self) -> None:
        """Poll for new log entries from the DB."""
        try:
            from database.connection import execute_query

            level_filter = {
                "all": None,
                "info": ("DEBUG", "INFO", "WARNING", "ERROR"),
                "warn": ("WARNING", "ERROR"),
            }.get(self._filter)

            if level_filter:
                rows = execute_query(
                    """SELECT * FROM bot_log WHERE id > ? AND level IN ({})
                       ORDER BY timestamp ASC LIMIT 100""".format(
                        ",".join("?" * len(level_filter))
                    ),
                    (self._last_log_id,) + tuple(level_filter),
                )
            else:
                rows = execute_query(
                    "SELECT * FROM bot_log WHERE id > ? ORDER BY timestamp ASC LIMIT 100",
                    (self._last_log_id,),
                )

            if not rows:
                return

            log = self.query_one("#activity_log", RichLog)
            for row in rows:
                self._last_log_id = max(self._last_log_id, row["id"])
                self._write_log_entry(log, row)

        except Exception:
            pass

    def _write_log_entry(self, log: RichLog, row) -> None:
        """Format and write a log entry with appropriate colors."""
        level = row["level"] or "INFO"
        module = row["module"] or "engine"
        message = row["message"] or ""
        ts = (row["timestamp"] or "")[:19].replace("T", " ")

        level_color = self.LEVEL_COLORS.get(level, "white")

        # Check for keyword highlighting
        msg_upper = message.upper()
        msg_color = level_color
        for keyword, kcolor in self.KEYWORD_COLORS.items():
            if keyword in msg_upper:
                msg_color = kcolor
                break

        log.write(
            f"[dim]{ts}[/dim] "
            f"[{level_color}]{level:7}[/{level_color}] "
            f"[dim]{module:12}[/dim] "
            f"[{msg_color}]{message}[/{msg_color}]"
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id

        if btn_id == "toggle_scroll_btn":
            self._auto_scroll = not self._auto_scroll
            btn = self.query_one("#toggle_scroll_btn", Button)
            btn.label = "Resume Scroll" if not self._auto_scroll else "Pause Scroll"

        elif btn_id == "clear_log_btn":
            log = self.query_one("#activity_log", RichLog)
            log.clear()

        elif btn_id == "log_filter_all":
            self._filter = "all"
            self._last_log_id = 0
            self.query_one("#activity_log", RichLog).clear()

        elif btn_id == "log_filter_info":
            self._filter = "info"
            self._last_log_id = 0
            self.query_one("#activity_log", RichLog).clear()

        elif btn_id == "log_filter_warn":
            self._filter = "warn"
            self._last_log_id = 0
            self.query_one("#activity_log", RichLog).clear()
