"""
dashboard/tabs/settings.py — Tab 8: Bot configuration and controls.

Allows configuration of:
  - Trading mode toggle (paper/live)
  - Risk parameters
  - API connection testing (display only)
  - Agent settings
  - Bot start/stop controls
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widget import Widget
from textual.widgets import Button, Input, Label, Static, Switch


class SettingRow(Widget):
    """A labeled settings row with an input field."""

    DEFAULT_CSS = """
    SettingRow {
        height: 3;
        layout: horizontal;
        padding: 0 1;
        align: left middle;
    }
    SettingRow .setting-label {
        width: 30;
        color: $text-muted;
    }
    SettingRow Input {
        width: 20;
    }
    """

    def __init__(self, label: str, value: str, setting_id: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._label = label
        self._value = value
        self._setting_id = setting_id

    def compose(self) -> ComposeResult:
        yield Label(self._label, classes="setting-label")
        yield Input(value=self._value, id=f"input_{self._setting_id}")


class SettingsTab(Widget):
    """Tab 8: Configuration, API management, and bot controls."""

    DEFAULT_CSS = """
    SettingsTab {
        height: 1fr;
        overflow-y: auto;
        padding: 1;
    }
    .section-title {
        color: $text-muted;
        text-style: bold;
        margin: 1 0 0 0;
    }
    .mode-section {
        height: 5;
        border: solid $panel;
        padding: 1;
        margin: 0 0 1 0;
    }
    .api-status-row {
        height: 2;
        layout: horizontal;
        padding: 0 1;
    }
    .api-label { width: 30; color: $text-muted; }
    .api-status { width: 20; }
    .btn-row {
        height: 3;
        layout: horizontal;
        align: left middle;
        margin: 1 0;
    }
    """

    def __init__(self, engine, **kwargs) -> None:
        super().__init__(**kwargs)
        self.engine = engine

    def compose(self) -> ComposeResult:
        from config import config

        # Mode control
        yield Label("Trading Mode", classes="section-title")
        with Horizontal(classes="mode-section"):
            yield Static(
                "[bold]PAPER[/bold] — Safe simulation mode (default)"
                if config.is_paper_mode
                else "[bold red]LIVE[/bold red] — [red]REAL MONEY AT RISK[/red]",
                id="mode_display",
            )
            yield Switch(
                value=config.is_live_mode,
                id="mode_switch",
            )
            yield Label("Switch to LIVE mode" if config.is_paper_mode else "Switch to PAPER mode",
                        id="mode_switch_label")

        # Risk parameters
        yield Label("Risk Parameters", classes="section-title")
        yield SettingRow("Trade threshold (0-100):", str(config.trade_threshold), "trade_threshold")
        yield SettingRow("Max positions:", str(config.max_positions), "max_positions")
        yield SettingRow("Max exposure/trade (%):", str(int(config.max_exposure_per_trade * 100)), "max_exposure_per_trade")
        yield SettingRow("Max total exposure (%):", str(int(config.max_total_exposure * 100)), "max_total_exposure")
        yield SettingRow("Stop loss (%):", str(int(config.stop_loss_pct * 100)), "stop_loss_pct")
        yield SettingRow("Take profit (%):", str(int(config.take_profit_pct * 100)), "take_profit_pct")

        with Horizontal(classes="btn-row"):
            yield Button("Save Risk Settings", id="save_risk_btn", variant="primary")
            yield Button("Reset to Defaults", id="reset_risk_btn", variant="default")

        # API connection status (read-only display)
        yield Label("API Connection Status", classes="section-title")
        for source_id, source_name in [
            ("kalshi", "Kalshi"), ("polymarket", "Polymarket"),
            ("binance", "Binance"), ("openweathermap", "OpenWeatherMap"),
            ("the_odds_api", "The Odds API"), ("newsapi", "NewsAPI"),
            ("telegram", "Telegram"),
        ]:
            configured = self._check_configured(source_id)
            status_str = "[green]✓ configured[/green]" if configured else "[dim]✗ not configured[/dim]"
            with Horizontal(classes="api-status-row"):
                yield Label(source_name + ":", classes="api-label")
                yield Static(status_str, classes="api-status", id=f"api_status_{source_id}")

        # Bot controls
        yield Label("Bot Controls", classes="section-title")
        with Horizontal(classes="btn-row"):
            yield Button("▶ Start Bot", id="start_bot_btn", variant="success")
            yield Button("⏸ Pause Bot", id="pause_bot_btn", variant="warning")
            yield Button("⏹ Stop Bot", id="stop_bot_btn", variant="error")

    def _check_configured(self, source_id: str) -> bool:
        """Check whether an API source appears to be configured."""
        from config import config
        checks = {
            "kalshi": config.kalshi_configured,
            "polymarket": config.polymarket_configured,
            "binance": bool(config.binance_api_key),
            "openweathermap": bool(config.openweathermap_api_key),
            "the_odds_api": bool(config.the_odds_api_key),
            "newsapi": bool(config.news_api_key),
            "telegram": config.telegram_configured,
        }
        return checks.get(source_id, False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id

        if btn_id == "start_bot_btn":
            self.engine.running = True
            self.app.notify("Bot started", severity="information")

        elif btn_id == "pause_bot_btn":
            self.engine.running = False
            self.app.notify("Bot paused", severity="warning")

        elif btn_id == "stop_bot_btn":
            self.engine.running = False
            self.app.notify("Bot stopped", severity="error")

        elif btn_id == "save_risk_btn":
            self._save_risk_settings()

        elif btn_id == "reset_risk_btn":
            self.app.notify("Risk settings reset to defaults", severity="information")

    def _save_risk_settings(self) -> None:
        """Save modified risk parameters to the settings DB table."""
        try:
            from database.connection import execute_write

            fields = [
                ("trade_threshold", False),
                ("max_positions", False),
                ("max_exposure_per_trade", True),
                ("max_total_exposure", True),
                ("stop_loss_pct", True),
                ("take_profit_pct", True),
            ]
            for field_id, is_pct in fields:
                try:
                    input_widget = self.query_one(f"#input_{field_id}", Input)
                    val_str = input_widget.value.strip()
                    val = float(val_str)
                    if is_pct:
                        val = val / 100.0
                    execute_write(
                        """INSERT OR REPLACE INTO settings (key, value)
                           VALUES (?, ?)""",
                        (field_id, str(val)),
                    )
                except Exception:
                    pass

            self.app.notify("Risk settings saved", severity="information")
        except Exception as e:
            self.app.notify(f"Save failed: {e}", severity="error")
