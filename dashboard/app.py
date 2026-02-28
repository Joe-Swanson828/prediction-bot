"""
dashboard/app.py — Main Textual terminal UI application.

Hosts all 9 dashboard tabs in a TabbedContent layout.
Runs concurrently with the trading engine via asyncio.TaskGroup.

Keyboard shortcuts:
  1-9     Switch between tabs
  q       Quit
  p       Panic close all positions (with confirmation)
  r       Force refresh current tab

The app receives a reference to the TradingEngine and passes it to
each tab so they can query live data directly.
"""

from __future__ import annotations

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Header, TabbedContent, TabPane

from dashboard.tabs.active_markets import ActiveMarketsTab
from dashboard.tabs.active_positions import ActivePositionsTab
from dashboard.tabs.agent_insights import AgentInsightsTab
from dashboard.tabs.bot_activity import BotActivityTab
from dashboard.tabs.data_feeds import DataFeedsTab
from dashboard.tabs.overview import OverviewTab
from dashboard.tabs.settings import SettingsTab
from dashboard.tabs.signal_log import SignalLogTab
from dashboard.tabs.trade_history import TradeHistoryTab


class TradingBotApp(App):
    """
    Main terminal dashboard for the Prediction Market Trading Bot.

    All 9 tabs are visible and navigable via keyboard or mouse.
    The engine reference enables tabs to query live trading data.
    """

    TITLE = "Prediction Market Trading Bot"
    SUB_TITLE = "Paper Mode"

    CSS = """
    Screen {
        background: #0d1117;
    }
    Header {
        background: #161b22;
        color: #58a6ff;
    }
    Footer {
        background: #161b22;
        color: #8b949e;
    }
    TabbedContent {
        height: 1fr;
    }
    TabPane {
        padding: 0;
    }
    /* Global text colors */
    .text-muted { color: #8b949e; }
    .text-success { color: #3fb950; }
    .text-danger { color: #f85149; }
    .text-warning { color: #d29922; }
    .text-info { color: #58a6ff; }

    /* Panel borders */
    $panel: #30363d;
    """

    BINDINGS = [
        Binding("1", "switch_tab('overview')", "Overview"),
        Binding("2", "switch_tab('markets')", "Markets"),
        Binding("3", "switch_tab('history')", "History"),
        Binding("4", "switch_tab('positions')", "Positions"),
        Binding("5", "switch_tab('signals')", "Signals"),
        Binding("6", "switch_tab('feeds')", "Feeds"),
        Binding("7", "switch_tab('agent')", "Agent"),
        Binding("8", "switch_tab('settings')", "Settings"),
        Binding("9", "switch_tab('log')", "Log"),
        Binding("q", "quit", "Quit"),
        Binding("p", "panic_close", "Panic Close", show=True),
        Binding("r", "refresh_tab", "Refresh", show=False),
    ]

    def __init__(self, engine, **kwargs) -> None:
        super().__init__(**kwargs)
        self.engine = engine
        self._update_subtitle()

    def _update_subtitle(self) -> None:
        """Update sub-title based on current trading mode."""
        mode = self.engine.config.trading_mode.upper()
        if mode == "LIVE":
            self.SUB_TITLE = "⚠ LIVE MODE — REAL MONEY"
        else:
            self.SUB_TITLE = f"Paper Mode | Balance: ${self.engine.paper_trader.balance:.2f}"

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)

        with TabbedContent(initial="overview", id="main_tabs"):
            with TabPane("1 Overview", id="overview"):
                yield OverviewTab(self.engine)

            with TabPane("2 Markets", id="markets"):
                yield ActiveMarketsTab(self.engine)

            with TabPane("3 History", id="history"):
                yield TradeHistoryTab(self.engine)

            with TabPane("4 Positions", id="positions"):
                yield ActivePositionsTab(self.engine)

            with TabPane("5 Signals", id="signals"):
                yield SignalLogTab(self.engine)

            with TabPane("6 Feeds", id="feeds"):
                yield DataFeedsTab(self.engine)

            with TabPane("7 Agent", id="agent"):
                yield AgentInsightsTab(self.engine)

            with TabPane("8 Settings", id="settings"):
                yield SettingsTab(self.engine)

            with TabPane("9 Log", id="log"):
                yield BotActivityTab(self.engine)

        yield Footer()

    def on_mount(self) -> None:
        """Start periodic subtitle update."""
        self.set_interval(5.0, self._refresh_subtitle)

    def _refresh_subtitle(self) -> None:
        """Keep subtitle current with balance."""
        try:
            balance = self.engine.paper_trader.balance
            mode = self.engine.config.trading_mode.upper()
            if mode == "PAPER":
                self.sub_title = f"Paper Mode | Balance: ${balance:.2f}"
            else:
                self.sub_title = "⚠ LIVE MODE — REAL MONEY AT RISK"
        except Exception:
            pass

    def action_switch_tab(self, tab_id: str) -> None:
        """Switch to the specified tab by ID."""
        try:
            self.query_one("#main_tabs", TabbedContent).active = tab_id
        except Exception:
            pass

    def action_panic_close(self) -> None:
        """Trigger panic close (close all positions immediately)."""
        import asyncio
        asyncio.create_task(self._do_panic_close())

    async def _do_panic_close(self) -> None:
        """Execute panic close with current market prices."""
        try:
            current_prices = {}
            for market in (self.engine.markets or []):
                market_id = market.get("id") or f"{market.get('exchange')}:{market.get('ticker')}"
                yes_price = market.get("yes_price") or market.get("yes_ask", 0.5)
                current_prices[market_id] = float(yes_price)

            result = await self.engine.paper_trader.panic_close_all(current_prices)
            pnl = result.get("total_pnl", 0.0)
            count = len(result.get("markets_closed", []))
            pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"

            self.notify(
                f"PANIC CLOSE: {count} positions closed | P&L: {pnl_str}",
                severity="warning" if pnl < 0 else "information",
            )

            if self.engine.notifier:
                await self.engine.notifier.notify_panic_close(pnl, count)
        except Exception as e:
            self.notify(f"Panic close failed: {e}", severity="error")

    def action_refresh_tab(self) -> None:
        """Force-refresh the current tab."""
        self.refresh()
