"""
notifications/telegram.py — Telegram bot notification service.

Sends push notifications to a Telegram chat for:
  - Trade executions
  - Position closures
  - High-confidence signals
  - Error alerts
  - Daily performance summaries

The notifier is ALWAYS a silent no-op if not configured.
It NEVER crashes or blocks the trading engine on any failure.

Setup:
  1. Create a bot via @BotFather on Telegram → get TELEGRAM_BOT_TOKEN
  2. Message the bot and get your TELEGRAM_CHAT_ID
  3. Set both in .env

Usage:
    notifier = TelegramNotifier(token="...", chat_id="...")
    await notifier.notify_trade(trade)
"""

from __future__ import annotations

from typing import Optional


class TelegramNotifier:
    """
    Sends Telegram messages for important bot events.
    All methods are safe to call even when not configured.
    """

    def __init__(self, token: str = "", chat_id: str = "") -> None:
        self._token = token
        self._chat_id = chat_id
        self._configured = bool(token and chat_id)
        self._bot = None

        if self._configured:
            self._init_bot()

    def _init_bot(self) -> None:
        """Try to initialize the Telegram bot. Fails silently if not available."""
        try:
            import telegram
            self._bot = telegram.Bot(token=self._token)
        except ImportError:
            self._configured = False
        except Exception:
            self._configured = False

    async def send(self, message: str, parse_mode: str = "HTML") -> bool:
        """
        Send a message to the configured Telegram chat.

        Args:
            message: Message text (HTML formatting supported)
            parse_mode: 'HTML' or 'Markdown'

        Returns:
            True if sent successfully, False otherwise.
        """
        if not self._configured or not self._bot:
            return False

        try:
            await self._bot.send_message(
                chat_id=self._chat_id,
                text=message,
                parse_mode=parse_mode,
            )
            return True
        except Exception:
            # Never let a notification failure bubble up to the trading engine
            return False

    async def notify_bot_started(self, mode: str, balance: float) -> None:
        """Notify that the bot has started."""
        await self.send(
            f"🤖 <b>Trading Bot Started</b>\n"
            f"Mode: {mode.upper()}\n"
            f"Balance: ${balance:.2f}\n"
            f"Status: Paper trading active"
        )

    async def notify_trade(self, trade) -> None:
        """Notify of a new trade execution."""
        arrow = "📈" if trade.direction == "YES" else "📉"
        await self.send(
            f"{arrow} <b>TRADE EXECUTED</b>\n"
            f"Market: <code>{trade.market_id}</code>\n"
            f"Direction: {trade.direction}\n"
            f"Entry: ${trade.entry_price:.4f}\n"
            f"Quantity: {trade.quantity:.3f} contracts\n"
            f"Cost: ${trade.entry_price * trade.quantity:.2f}\n"
            f"Score: {trade.composite_score:.1f}/100\n"
            f"Mode: {trade.mode.upper()}\n"
            f"Slippage: {trade.slippage*100:.2f}%"
        )

    async def notify_position_closed(self, trade, pnl: float) -> None:
        """Notify that a position was closed."""
        emoji = "✅" if pnl >= 0 else "❌"
        pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
        await self.send(
            f"{emoji} <b>POSITION CLOSED</b>\n"
            f"Market: <code>{trade.market_id}</code>\n"
            f"Direction: {trade.direction}\n"
            f"P&L: <b>{pnl_str}</b>\n"
            f"Exit: ${trade.exit_price:.4f}"
        )

    async def notify_signal(
        self,
        market_id: str,
        signal_type: str,
        score: float,
        direction: str,
    ) -> None:
        """Notify of a high-confidence signal (score >= 80)."""
        if score < 80:
            return  # only notify for strong signals
        type_emoji = {"ta": "📊", "sentiment": "📰", "speed": "⚡"}.get(signal_type, "🔔")
        await self.send(
            f"{type_emoji} <b>STRONG SIGNAL</b>\n"
            f"Market: <code>{market_id}</code>\n"
            f"Type: {signal_type.upper()}\n"
            f"Score: {score:.1f}/100\n"
            f"Direction: {direction.capitalize()}"
        )

    async def notify_agent_adjustment(
        self, category: str, old_weights: dict, new_weights: dict, reason: str
    ) -> None:
        """Notify of an agent weight adjustment."""
        await self.send(
            f"🧠 <b>AGENT ADJUSTMENT</b>\n"
            f"Category: {category.capitalize()}\n"
            f"Old: TA={old_weights.get('ta', 0):.2f} "
            f"Sent={old_weights.get('sentiment', 0):.2f} "
            f"Speed={old_weights.get('speed', 0):.2f}\n"
            f"New: TA={new_weights.get('ta', 0):.2f} "
            f"Sent={new_weights.get('sentiment', 0):.2f} "
            f"Speed={new_weights.get('speed', 0):.2f}\n"
            f"Reason: {reason[:200]}"
        )

    async def notify_error(self, component: str, error_msg: str) -> None:
        """Notify of a critical error (condensed, no stack traces)."""
        await self.send(
            f"🚨 <b>ERROR</b>\n"
            f"Component: {component}\n"
            f"Message: {error_msg[:300]}"
        )

    async def notify_daily_summary(self, stats: dict) -> None:
        """Send a daily performance summary."""
        pnl = stats.get("total_pnl", 0.0)
        pnl_str = f"+${pnl:.2f}" if pnl >= 0 else f"-${abs(pnl):.2f}"
        emoji = "📈" if pnl >= 0 else "📉"

        await self.send(
            f"{emoji} <b>Daily Summary</b>\n"
            f"Total Trades: {stats.get('total_trades', 0)}\n"
            f"Win Rate: {stats.get('win_rate', 0):.1f}%\n"
            f"Today P&L: {pnl_str}\n"
            f"Balance: ${stats.get('balance', 0):.2f}"
        )

    async def notify_panic_close(self, total_pnl: float, count: int) -> None:
        """Notify of a panic close all event."""
        pnl_str = f"+${total_pnl:.2f}" if total_pnl >= 0 else f"-${abs(total_pnl):.2f}"
        await self.send(
            f"🚨 <b>PANIC CLOSE ALL</b>\n"
            f"Closed: {count} positions\n"
            f"Total P&L: {pnl_str}"
        )

    @property
    def is_configured(self) -> bool:
        """True when Telegram is set up and ready to send."""
        return self._configured
