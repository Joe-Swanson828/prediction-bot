"""
main.py — Prediction Market Trading Bot entry point.

Initializes all components and runs the trading engine loop
and Textual dashboard concurrently via asyncio.TaskGroup.

Architecture:
  TradingEngine owns all components and coordinates them.
  scan_loop() runs every 30 seconds, analyzing markets and executing trades.
  TradingBotApp runs in the same event loop via run_async().

Usage:
  python main.py

Requires:
  - Python 3.11+ (uses asyncio.TaskGroup)
  - .env file in current directory (optional for paper mode)
  - See .env.example for all available variables
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from config import config
from database.connection import initialize_db
from database.schema import create_all_tables


def _setup_logging() -> None:
    """Configure the Python logging system."""
    level = getattr(logging, config.log_level, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


class TradingEngine:
    """
    Main coordinator for the prediction market trading bot.

    Owns all components:
      - Exchange clients (Kalshi, Polymarket)
      - Data source clients (crypto, weather, sports, news)
      - Analysis engines (TA, sentiment, speed)
      - Engine layer (risk, signals, paper trading, agent)
      - Notification service (Telegram)

    The engine loop runs every 30 seconds, analyzing all monitored
    markets and executing paper trades when composite scores are high.
    """

    SCAN_INTERVAL: int = 30   # seconds between full market scans
    LOG_MODULE: str = "engine"

    def __init__(self) -> None:
        self.config = config
        self.running: bool = False
        self._start_time: float = 0.0

        # These are populated in initialize()
        self.markets: List[dict] = []
        self.latest_scores: Dict[str, dict] = {}

        # Component placeholders (initialized in initialize())
        self.kalshi = None
        self.polymarket = None
        self.crypto_feed = None
        self.weather_feed = None
        self.sports_feed = None
        self.news_feed = None
        self.ta = None
        self.sentiment_analyzer = None
        self.speed_monitor = None
        self.risk = None
        self.paper_trader = None
        self.aggregator = None
        self.agent = None
        self.notifier = None

    async def initialize(self) -> None:
        """Initialize all components and the database."""
        # Database first
        initialize_db()
        create_all_tables()
        self._log("INFO", "Database initialized")

        # Import components (deferred to avoid circular import issues)
        from analysis.sentiment import SentimentAnalyzer
        from analysis.speed import SpeedMonitor
        from analysis.technical import TechnicalAnalyzer
        from data_sources.crypto import CryptoDataSource
        from data_sources.news import NewsDataSource
        from data_sources.sports import SportsDataSource
        from data_sources.weather import WeatherDataSource
        from engine.agent import AgentEngine
        from engine.paper_trading import PaperTradingEngine
        from engine.risk import RiskManager
        from engine.signals import SignalAggregator
        from exchanges.kalshi import KalshiClient
        from exchanges.polymarket import PolymarketClient
        from notifications.telegram import TelegramNotifier

        # Exchange clients
        self.kalshi = KalshiClient(
            api_key_id=config.kalshi_api_key_id,
            private_key_path=config.kalshi_private_key_path,
        )
        self.polymarket = PolymarketClient(
            private_key=config.polymarket_private_key,
            funder_address=config.polymarket_funder_address,
        )

        # Data sources
        self.crypto_feed = CryptoDataSource()
        self.weather_feed = WeatherDataSource(config.openweathermap_api_key)
        self.sports_feed = SportsDataSource(config.the_odds_api_key)
        self.news_feed = NewsDataSource(config.news_api_key)

        # Analysis engines
        self.ta = TechnicalAnalyzer()
        self.sentiment_analyzer = SentimentAnalyzer()
        self.speed_monitor = SpeedMonitor()

        # Engine layer
        self.risk = RiskManager(starting_balance=config.paper_starting_balance)
        self.paper_trader = PaperTradingEngine(
            starting_balance=config.paper_starting_balance,
            risk=self.risk,
            log_callback=self._log,
        )
        self.aggregator = SignalAggregator()
        self.agent = AgentEngine()

        # Notifications (silent if not configured)
        self.notifier = TelegramNotifier(
            token=config.telegram_bot_token,
            chat_id=config.telegram_chat_id,
        )

        # Load initial market list
        self.markets = await self._fetch_all_markets()
        self._log("INFO", f"Loaded {len(self.markets)} markets across all exchanges and categories")

        # Notify startup
        if self.notifier.is_configured:
            await self.notifier.notify_bot_started(
                mode=config.trading_mode,
                balance=self.paper_trader.balance,
            )

        mode_str = "STUB (no API keys)" if self.kalshi.is_stub_mode else "LIVE API"
        self._log(
            "INFO",
            f"Bot initialized | Mode: {config.trading_mode.upper()} | "
            f"Kalshi: {mode_str} | Balance: ${self.paper_trader.balance:.2f}",
        )

    async def _fetch_all_markets(self) -> List[dict]:
        """
        Fetch and combine markets from all exchanges.
        Only returns markets in the three target categories:
        sports, crypto, and weather.
        """
        all_markets = []

        for category in ("sports", "crypto", "weather"):
            try:
                kalshi_markets = await self.kalshi.get_markets(category=category)
                for m in kalshi_markets:
                    market_id = f"kalshi:{m.get('ticker', 'unknown')}"
                    m["id"] = market_id
                    m["exchange"] = "kalshi"
                    m["category"] = category
                    # Normalize price field name
                    if "yes_ask" in m and "yes_price" not in m:
                        m["yes_price"] = m["yes_ask"]
                    all_markets.append(m)
                    # Upsert into DB
                    self._upsert_market(m, market_id, category)
            except Exception as e:
                self._log("WARNING", f"Could not fetch Kalshi {category} markets: {e}")

            try:
                poly_markets = await self.polymarket.get_markets(category=category)
                for m in poly_markets:
                    market_id = m.get("id") or f"polymarket:{m.get('ticker', 'unknown')}"
                    m["id"] = market_id
                    m["exchange"] = "polymarket"
                    m["category"] = category
                    all_markets.append(m)
                    self._upsert_market(m, market_id, category)
            except Exception as e:
                self._log("WARNING", f"Could not fetch Polymarket {category} markets: {e}")

        return all_markets

    def _upsert_market(self, market: dict, market_id: str, category: str) -> None:
        """Insert or update a market record in the database."""
        from database.connection import execute_write
        now = datetime.now(timezone.utc).isoformat()
        try:
            execute_write(
                """INSERT OR REPLACE INTO markets
                   (id, exchange, ticker, category, title,
                    yes_price, no_price, volume, open_interest,
                    close_date, status, last_updated)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    market_id,
                    market.get("exchange", "unknown"),
                    market.get("ticker", market_id),
                    category,
                    market.get("title", market_id),
                    float(market.get("yes_price", 0.5)),
                    float(market.get("no_price", 0.5)),
                    float(market.get("volume", 0)),
                    float(market.get("open_interest", 0)),
                    market.get("close_date") or market.get("close_time"),
                    market.get("status", "active"),
                    now,
                ),
            )
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # Main scan loop
    # ------------------------------------------------------------------ #

    async def scan_loop(self) -> None:
        """
        Main market analysis loop. Runs every SCAN_INTERVAL seconds.

        For each market:
          1. Fetch latest candlestick data
          2. Run TA analysis (with state machine)
          3. Fetch news headlines for sentiment
          4. Record speed/timing data
          5. Compute composite score
          6. Execute paper trade if score >= threshold and ≥2 signals agree
          7. Save all signals to DB
          8. Log the decision

        After all markets: check agent for weight adjustments.
        """
        while self.running:
            scan_start = time.time()
            self._log("INFO", f"Starting market scan — {len(self.markets)} markets")

            for market in self.markets:
                try:
                    await self._analyze_market(market)
                except Exception as e:
                    market_id = market.get("id", "unknown")
                    self._log("ERROR", f"Error analyzing {market_id}: {e}")

            # Check agent for weight adjustments
            try:
                await self.agent.maybe_evaluate()
            except Exception as e:
                self._log("ERROR", f"Agent evaluation error: {e}")

            scan_duration = time.time() - scan_start
            self._log(
                "DEBUG",
                f"Scan complete in {scan_duration:.1f}s | "
                f"Balance: ${self.paper_trader.balance:.2f} | "
                f"Open positions: {self.risk.position_count}",
            )

            # Wait for next scan interval
            await asyncio.sleep(max(0, self.SCAN_INTERVAL - scan_duration))

    async def _analyze_market(self, market: dict) -> None:
        """
        Run the full signal pipeline for a single market.
        """
        market_id = market.get("id", "unknown")
        category = market.get("category", "crypto")
        exchange = market.get("exchange", "kalshi")
        ticker = market.get("ticker", "")
        current_price = float(market.get("yes_price") or market.get("yes_ask", 0.5))

        # 1. Get candlestick data
        candles = []
        try:
            if exchange == "kalshi":
                candles = await self.kalshi.get_candlesticks(ticker, period_interval=60, limit=100)
            else:
                # For Polymarket: use crypto price data as proxy where applicable
                if category == "crypto":
                    symbol = "BTC" if "btc" in ticker.lower() or "bitcoin" in ticker.lower() else "ETH"
                    raw_candles = await self.crypto_feed.get_candles(symbol, interval="1h", limit=100)
                    # Normalize crypto prices to [0, 1] scale for TA (rough approximation)
                    if raw_candles:
                        max_price = max(c["close"] for c in raw_candles)
                        if max_price > 0:
                            candles = [
                                {**c, "open": c["open"]/max_price, "high": c["high"]/max_price,
                                 "low": c["low"]/max_price, "close": c["close"]/max_price}
                                for c in raw_candles
                            ]
        except Exception as e:
            self._log("DEBUG", f"Could not fetch candles for {market_id}: {e}")

        # 2. Run TA analysis
        ta_result = self.ta.analyze(market_id, candles)

        # 3. Update speed monitor with current price
        volume = float(market.get("volume", 0))
        self.speed_monitor.record_update(market_id, current_price, volume)

        # 4. Fetch news/sentiment
        sentiment_result = {"sentiment_score": 50.0, "direction": "neutral"}
        try:
            headlines = await self.news_feed.get_headlines(
                query=market.get("title", category),
                from_hours=12,
                max_results=10,
            )
            sentiment_result = self.sentiment_analyzer.analyze_market(
                market_id=market_id,
                category=category,
                news_items=headlines,
            )
        except Exception as e:
            self._log("DEBUG", f"Sentiment fetch failed for {market_id}: {e}")

        # 5. Compute speed score
        speed_result = self.speed_monitor.compute_speed_score(
            market_id=market_id,
            category=category,
            current_market_price=current_price,
        )

        # 6. Compute composite score
        composite = self.aggregator.compute_composite_score(
            market_id=market_id,
            category=category,
            ta_result=ta_result,
            sentiment_result=sentiment_result,
            speed_result=speed_result,
        )
        # Attach TA breakout state for dashboard display
        composite["ta_breakout_state"] = ta_result.get("breakout_state", "SCANNING")

        # Store latest score for dashboard
        self.latest_scores[market_id] = composite

        # 7. Save signals to DB
        try:
            self.aggregator.save_all_signals(market_id, composite)
        except Exception:
            pass

        # 8. Execute paper trade if eligible
        if composite["trade_eligible"] and self.running and config.is_paper_mode:
            direction = "YES" if composite["recommendation"] == "BUY_YES" else "NO"
            trade = await self.paper_trader.execute_trade(
                market_id=market_id,
                direction=direction,
                current_price=current_price,
                composite_score=composite,
            )

            if trade and self.notifier.is_configured:
                await self.notifier.notify_trade(trade)

        # 9. Log the decision
        score = composite["final_score"]
        recommendation = composite["recommendation"]
        self._log(
            "INFO" if score >= 50 else "DEBUG",
            f"{market_id[:30]:30} | "
            f"TA={ta_result['ta_score']:.0f} "
            f"Sent={sentiment_result['sentiment_score']:.0f} "
            f"Speed={speed_result['speed_score']:.0f} "
            f"→ Final={score:.1f} "
            f"[{recommendation}]",
        )

    # ------------------------------------------------------------------ #
    # Engine lifecycle
    # ------------------------------------------------------------------ #

    async def run(self) -> None:
        """
        Start the trading engine and dashboard concurrently.
        Uses asyncio.TaskGroup (Python 3.11+) for proper task lifecycle.
        """
        _setup_logging()
        await self.initialize()
        self.running = True
        self._start_time = time.time()

        from dashboard.app import TradingBotApp
        app = TradingBotApp(engine=self)

        self._log("INFO", "Starting dashboard and scan loop")

        try:
            async with asyncio.TaskGroup() as tg:
                tg.create_task(self.scan_loop(), name="scan_loop")
                tg.create_task(app.run_async(), name="dashboard")
        except* KeyboardInterrupt:
            pass
        except* asyncio.CancelledError:
            pass
        finally:
            self.running = False
            await self._shutdown()

    async def _shutdown(self) -> None:
        """Clean shutdown of all components."""
        self._log("INFO", "Shutting down trading engine")
        try:
            if self.kalshi:
                await self.kalshi.close()
            if self.crypto_feed:
                await self.crypto_feed.close()
            if self.weather_feed:
                await self.weather_feed.close()
            if self.sports_feed:
                await self.sports_feed.close()
            if self.news_feed:
                await self.news_feed.close()
        except Exception:
            pass

        from database.connection import close_connection
        close_connection()

    # ------------------------------------------------------------------ #
    # Logging helper
    # ------------------------------------------------------------------ #

    def _log(self, level: str, message: str) -> None:
        """
        Write to both the Python logging system and the bot_log DB table.
        Never raises — logging must not crash the engine.
        """
        # Python logger
        logger = logging.getLogger(self.LOG_MODULE)
        log_method = getattr(logger, level.lower(), logger.info)
        log_method(message)

        # DB log (for dashboard display)
        from engine.signals import SignalAggregator
        SignalAggregator.log_to_db(level, self.LOG_MODULE, message)


# ------------------------------------------------------------------ #
# Entry point
# ------------------------------------------------------------------ #

def main() -> None:
    """Application entry point."""
    print("Prediction Market Trading Bot")
    print(f"Mode: {config.trading_mode.upper()}")
    print(f"Python: {sys.version.split()[0]}")
    print(f"DB: {config.db_path}")
    print()

    if config.is_live_mode:
        print("⚠  WARNING: LIVE TRADING MODE IS ACTIVE")
        print("   Real money will be used for trades.")
        print("   Press Ctrl+C to abort.")
        print()

    engine = TradingEngine()
    asyncio.run(engine.run())


if __name__ == "__main__":
    main()
