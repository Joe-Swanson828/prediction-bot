"""
dashboard/tabs/data_feeds.py — Tab 6: Data source health status board.

Shows the connection status of all external APIs:
  ✓ healthy  — last call successful
  ⚠ degraded — errors but still responding
  ✗ down     — consecutive failures
  ? unknown  — not yet called
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import DataTable, Static


class DataFeedsTab(Widget):
    """Tab 6: Health status for all external data sources."""

    DEFAULT_CSS = """
    DataFeedsTab {
        height: 1fr;
        overflow-y: auto;
    }
    .feeds-header {
        height: 2;
        padding: 0 1;
        color: $text-muted;
    }
    DataTable {
        height: 1fr;
    }
    """

    COLUMNS = [
        ("Source", 20),
        ("Status", 10),
        ("Last Success", 20),
        ("Last Error", 35),
        ("Errors", 8),
        ("Latency", 10),
    ]

    # Default sources to seed if DB is empty
    DEFAULT_SOURCES = [
        ("kalshi_rest", "Kalshi REST API"),
        ("kalshi_ws", "Kalshi WebSocket"),
        ("polymarket_clob", "Polymarket CLOB"),
        ("binance", "Binance (Crypto)"),
        ("openweathermap", "OpenWeatherMap"),
        ("the_odds_api", "The Odds API"),
        ("newsapi", "NewsAPI"),
    ]

    def __init__(self, engine, **kwargs) -> None:
        super().__init__(**kwargs)
        self.engine = engine

    def compose(self) -> ComposeResult:
        yield Static(
            "Data Feed Status — refreshes every 30 seconds",
            classes="feeds-header",
        )
        yield DataTable(id="feeds_table", zebra_stripes=True)

    def on_mount(self) -> None:
        table = self.query_one("#feeds_table", DataTable)
        for name, width in self.COLUMNS:
            table.add_column(name, width=width)
        self._seed_default_sources()
        self.set_interval(30.0, self.refresh_data)
        self.refresh_data()

    def _seed_default_sources(self) -> None:
        """Insert default source records into DB if missing."""
        try:
            from database.connection import execute_query, execute_write
            for source_id, source_name in self.DEFAULT_SOURCES:
                existing = execute_query(
                    "SELECT id FROM data_source_status WHERE id=?", (source_id,)
                )
                if not existing:
                    execute_write(
                        """INSERT OR IGNORE INTO data_source_status
                           (id, source_name, status) VALUES (?, ?, 'unknown')""",
                        (source_id, source_name),
                    )
        except Exception:
            pass

    def refresh_data(self) -> None:
        """Reload source statuses from DB."""
        try:
            from database.connection import execute_query

            table = self.query_one("#feeds_table", DataTable)
            table.clear()

            rows = execute_query(
                "SELECT * FROM data_source_status ORDER BY source_name"
            )

            if not rows:
                self._seed_default_sources()
                return

            for row in rows:
                status = row["status"] or "unknown"
                status_display = {
                    "healthy": "[green]✓ healthy[/green]",
                    "degraded": "[yellow]⚠ degraded[/yellow]",
                    "down": "[red]✗ down[/red]",
                    "unknown": "[dim]? unknown[/dim]",
                }.get(status, "[dim]?[/dim]")

                last_success = (row["last_success"] or "never")[:19].replace("T", " ")
                last_error = (row["last_error"] or "—")[:33]
                errors = str(row["error_count"] or 0)
                latency = f"{row['latency_ms']:.0f}ms" if row["latency_ms"] else "—"

                table.add_row(
                    row["source_name"],
                    status_display,
                    last_success,
                    last_error,
                    errors,
                    latency,
                )
        except Exception:
            pass
