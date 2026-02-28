"""
config.py — Configuration singleton for the Prediction Market Trading Bot.

Loads all settings from environment variables (via .env file) and exposes
a single `config` object imported throughout the codebase:

    from config import config

Never hardcode sensitive values. All secrets live in .env.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from typing import Dict

from dotenv import load_dotenv

# Load .env file at import time (safe to call multiple times)
load_dotenv()


@dataclass
class WeightConfig:
    """Signal type weights for a given market category. Must sum to 1.0."""

    ta_weight: float
    sentiment_weight: float
    speed_weight: float

    def __post_init__(self) -> None:
        total = self.ta_weight + self.sentiment_weight + self.speed_weight
        if abs(total - 1.0) > 0.01:
            raise ValueError(
                f"Weights must sum to 1.0, got {total:.3f} "
                f"(ta={self.ta_weight}, sentiment={self.sentiment_weight}, speed={self.speed_weight})"
            )

    def as_dict(self) -> dict:
        """Return weights as a plain dict."""
        return {
            "ta": self.ta_weight,
            "sentiment": self.sentiment_weight,
            "speed": self.speed_weight,
        }


def _get_float(key: str, default: float) -> float:
    """Read an env var as float, falling back to default."""
    val = os.getenv(key)
    if val is None:
        return default
    try:
        return float(val)
    except ValueError:
        print(f"[config] WARNING: {key}={val!r} is not a valid float, using default {default}", file=sys.stderr)
        return default


def _get_int(key: str, default: int) -> int:
    """Read an env var as int, falling back to default."""
    val = os.getenv(key)
    if val is None:
        return default
    try:
        return int(val)
    except ValueError:
        print(f"[config] WARNING: {key}={val!r} is not a valid int, using default {default}", file=sys.stderr)
        return default


@dataclass
class Config:
    """
    Central configuration object. Populated from environment variables.
    Access via the module-level `config` singleton.
    """

    # ------------------------------------------------------------------ #
    # Trading mode
    # ------------------------------------------------------------------ #
    trading_mode: str = "paper"           # 'paper' | 'live'
    paper_starting_balance: float = 100.0

    # ------------------------------------------------------------------ #
    # Kalshi API
    # ------------------------------------------------------------------ #
    kalshi_api_key_id: str = ""
    kalshi_private_key_path: str = "./kalshi_private_key.pem"
    kalshi_base_url: str = "https://api.elections.kalshi.com/trade-api/v2"
    kalshi_ws_url: str = "wss://api.elections.kalshi.com/trade-api/ws/v2"

    # ------------------------------------------------------------------ #
    # Polymarket API
    # ------------------------------------------------------------------ #
    polymarket_private_key: str = ""
    polymarket_funder_address: str = ""
    polymarket_clob_url: str = "https://clob.polymarket.com"
    polymarket_gamma_url: str = "https://gamma-api.polymarket.com"

    # ------------------------------------------------------------------ #
    # Crypto exchange APIs
    # ------------------------------------------------------------------ #
    binance_api_key: str = ""
    binance_api_secret: str = ""
    coingecko_api_key: str = ""

    # ------------------------------------------------------------------ #
    # Weather APIs
    # ------------------------------------------------------------------ #
    openweathermap_api_key: str = ""
    weatherapi_key: str = ""

    # ------------------------------------------------------------------ #
    # Sports APIs
    # ------------------------------------------------------------------ #
    the_odds_api_key: str = ""
    sportsradar_api_key: str = ""

    # ------------------------------------------------------------------ #
    # News & Sentiment APIs
    # ------------------------------------------------------------------ #
    news_api_key: str = ""
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = "PredictionBot/1.0"
    twitter_bearer_token: str = ""

    # ------------------------------------------------------------------ #
    # Telegram notifications
    # ------------------------------------------------------------------ #
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # ------------------------------------------------------------------ #
    # Risk parameters
    # ------------------------------------------------------------------ #
    max_positions: int = 5
    max_exposure_per_trade: float = 0.20   # fraction of balance
    max_total_exposure: float = 0.80       # fraction of balance
    trade_threshold: int = 65              # composite score 0-100
    stop_loss_pct: float = 0.15            # 15% below entry
    take_profit_pct: float = 0.30          # 30% above entry

    # ------------------------------------------------------------------ #
    # Miscellaneous
    # ------------------------------------------------------------------ #
    sentiment_model: str = "vader"         # 'vader' | 'finbert'
    log_level: str = "INFO"
    db_path: str = "./trading_bot.db"

    # ------------------------------------------------------------------ #
    # Default signal type weights by market category
    # Adjusted by the agent over time; stored in strategy_weights DB table
    # ------------------------------------------------------------------ #
    category_weights: Dict[str, WeightConfig] = field(default_factory=lambda: {
        "sports":  WeightConfig(ta_weight=0.20, sentiment_weight=0.35, speed_weight=0.45),
        "crypto":  WeightConfig(ta_weight=0.40, sentiment_weight=0.30, speed_weight=0.30),
        "weather": WeightConfig(ta_weight=0.15, sentiment_weight=0.05, speed_weight=0.80),
    })

    @property
    def is_paper_mode(self) -> bool:
        """True when running in paper trading mode (default)."""
        return self.trading_mode.lower() == "paper"

    @property
    def is_live_mode(self) -> bool:
        """True only when explicitly set to live mode."""
        return self.trading_mode.lower() == "live"

    @property
    def kalshi_configured(self) -> bool:
        """True if Kalshi credentials appear to be set."""
        return bool(self.kalshi_api_key_id and os.path.exists(self.kalshi_private_key_path))

    @property
    def polymarket_configured(self) -> bool:
        """True if Polymarket credentials appear to be set."""
        return bool(self.polymarket_private_key)

    @property
    def telegram_configured(self) -> bool:
        """True if Telegram bot token and chat ID are set."""
        return bool(self.telegram_bot_token and self.telegram_chat_id)

    def get_weights(self, category: str) -> WeightConfig:
        """
        Return signal type weights for a given category.
        Falls back to even weights if category is unknown.
        """
        return self.category_weights.get(
            category.lower(),
            WeightConfig(ta_weight=0.33, sentiment_weight=0.33, speed_weight=0.34),
        )


def _build_config() -> Config:
    """Build the Config singleton from environment variables."""
    mode = os.getenv("TRADING_MODE", "paper").strip().lower()
    if mode not in ("paper", "live"):
        print(f"[config] WARNING: TRADING_MODE={mode!r} is invalid, defaulting to 'paper'", file=sys.stderr)
        mode = "paper"

    return Config(
        trading_mode=mode,
        paper_starting_balance=_get_float("PAPER_STARTING_BALANCE", 100.0),

        # Kalshi
        kalshi_api_key_id=os.getenv("KALSHI_API_KEY_ID", ""),
        kalshi_private_key_path=os.getenv("KALSHI_PRIVATE_KEY_PATH", "./kalshi_private_key.pem"),

        # Polymarket
        polymarket_private_key=os.getenv("POLYMARKET_PRIVATE_KEY", ""),
        polymarket_funder_address=os.getenv("POLYMARKET_FUNDER_ADDRESS", ""),

        # Crypto
        binance_api_key=os.getenv("BINANCE_API_KEY", ""),
        binance_api_secret=os.getenv("BINANCE_API_SECRET", ""),
        coingecko_api_key=os.getenv("COINGECKO_API_KEY", ""),

        # Weather
        openweathermap_api_key=os.getenv("OPENWEATHERMAP_API_KEY", ""),
        weatherapi_key=os.getenv("WEATHERAPI_KEY", ""),

        # Sports
        the_odds_api_key=os.getenv("THE_ODDS_API_KEY", ""),
        sportsradar_api_key=os.getenv("SPORTSRADAR_API_KEY", ""),

        # News
        news_api_key=os.getenv("NEWS_API_KEY", ""),
        reddit_client_id=os.getenv("REDDIT_CLIENT_ID", ""),
        reddit_client_secret=os.getenv("REDDIT_CLIENT_SECRET", ""),
        reddit_user_agent=os.getenv("REDDIT_USER_AGENT", "PredictionBot/1.0"),
        twitter_bearer_token=os.getenv("TWITTER_BEARER_TOKEN", ""),

        # Telegram
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),

        # Risk
        max_positions=_get_int("MAX_POSITIONS", 5),
        max_exposure_per_trade=_get_float("MAX_EXPOSURE_PER_TRADE", 0.20),
        max_total_exposure=_get_float("MAX_TOTAL_EXPOSURE", 0.80),
        trade_threshold=_get_int("TRADE_THRESHOLD", 65),
        stop_loss_pct=_get_float("STOP_LOSS_PCT", 0.15),
        take_profit_pct=_get_float("TAKE_PROFIT_PCT", 0.30),

        # Misc
        sentiment_model=os.getenv("SENTIMENT_MODEL", "vader").strip().lower(),
        log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper(),
        db_path=os.getenv("DB_PATH", "./trading_bot.db"),
    )


# Module-level singleton — import this everywhere:
#   from config import config
config: Config = _build_config()
