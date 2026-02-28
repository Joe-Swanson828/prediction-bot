"""
database/schema.py — Database table definitions and migration support.

Defines all 13 tables used by the trading bot. Call create_all_tables()
once at application startup to ensure all tables exist.

Tables:
    1.  markets            — tracked prediction markets
    2.  candlesticks       — OHLCV price data per market
    3.  signals            — individual TA/sentiment/speed signals
    4.  composite_scores   — aggregated scores that drove decisions
    5.  trades             — all executed trades (paper and live)
    6.  positions          — currently open positions
    7.  balance_history    — periodic balance snapshots (equity curve)
    8.  agent_log          — weight adjustment history from the agent
    9.  data_source_status — health tracking for external APIs
    10. sentiment_cache    — cached NLP results to avoid re-processing
    11. bot_log            — timestamped log of all bot actions
    12. settings           — key-value store for runtime configuration
    13. strategy_weights   — current and historical signal weights per category
"""

from __future__ import annotations

from database.connection import get_db

# SQL statements for all tables. Using IF NOT EXISTS for idempotency.
_SCHEMA_STATEMENTS = [
    # ------------------------------------------------------------------ #
    # 1. Markets — prediction markets being monitored
    # ------------------------------------------------------------------ #
    """
    CREATE TABLE IF NOT EXISTS markets (
        id              TEXT PRIMARY KEY,     -- e.g. "kalshi:KXBTCX-25DEC"
        exchange        TEXT NOT NULL,        -- 'kalshi' | 'polymarket'
        ticker          TEXT NOT NULL,
        category        TEXT NOT NULL,        -- 'sports' | 'crypto' | 'weather'
        title           TEXT NOT NULL,
        yes_price       REAL DEFAULT 0.5,     -- current YES contract price [0, 1]
        no_price        REAL DEFAULT 0.5,     -- current NO contract price [0, 1]
        volume          REAL DEFAULT 0,
        open_interest   REAL DEFAULT 0,
        close_date      TEXT,                 -- ISO 8601 expiry datetime
        status          TEXT DEFAULT 'active',-- 'active' | 'closed' | 'settled'
        last_updated    TEXT DEFAULT (datetime('now'))
    )
    """,

    # ------------------------------------------------------------------ #
    # 2. Candlesticks — OHLCV price history for TA
    # ------------------------------------------------------------------ #
    """
    CREATE TABLE IF NOT EXISTS candlesticks (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        market_id   TEXT NOT NULL REFERENCES markets(id),
        timestamp   TEXT NOT NULL,            -- ISO 8601
        open        REAL,
        high        REAL,
        low         REAL,
        close       REAL,
        volume      REAL DEFAULT 0,
        period_min  INTEGER DEFAULT 1,        -- candle period in minutes
        UNIQUE(market_id, timestamp, period_min)
    )
    """,

    # ------------------------------------------------------------------ #
    # 3. Signals — individual detected signals (before aggregation)
    # ------------------------------------------------------------------ #
    """
    CREATE TABLE IF NOT EXISTS signals (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        market_id   TEXT NOT NULL REFERENCES markets(id),
        signal_type TEXT NOT NULL,            -- 'ta' | 'sentiment' | 'speed'
        signal_name TEXT NOT NULL,            -- e.g. 'double_breakout', 'vader_score'
        value       REAL NOT NULL,            -- score 0-100
        direction   TEXT,                     -- 'bullish' | 'bearish' | 'neutral'
        confidence  REAL,                     -- 0-100
        metadata    TEXT,                     -- JSON blob with raw details
        acted_on    INTEGER DEFAULT 0,        -- 1 if a trade was taken
        timestamp   TEXT DEFAULT (datetime('now'))
    )
    """,

    # ------------------------------------------------------------------ #
    # 4. Composite scores — aggregated scores that drove each decision
    # ------------------------------------------------------------------ #
    """
    CREATE TABLE IF NOT EXISTS composite_scores (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        market_id           TEXT NOT NULL REFERENCES markets(id),
        ta_score            REAL DEFAULT 0,
        sentiment_score     REAL DEFAULT 0,
        speed_score         REAL DEFAULT 0,
        ta_weight           REAL NOT NULL,
        sentiment_weight    REAL NOT NULL,
        speed_weight        REAL NOT NULL,
        final_score         REAL NOT NULL,
        recommendation      TEXT,             -- 'BUY_YES' | 'BUY_NO' | 'HOLD'
        timestamp           TEXT DEFAULT (datetime('now'))
    )
    """,

    # ------------------------------------------------------------------ #
    # 5. Trades — all executed trades (paper and live)
    # ------------------------------------------------------------------ #
    """
    CREATE TABLE IF NOT EXISTS trades (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        market_id           TEXT NOT NULL REFERENCES markets(id),
        exchange            TEXT NOT NULL,
        direction           TEXT NOT NULL,    -- 'YES' | 'NO'
        quantity            REAL NOT NULL,    -- number of contracts
        entry_price         REAL NOT NULL,    -- price paid per contract
        exit_price          REAL,             -- price received on close
        entry_time          TEXT NOT NULL,
        exit_time           TEXT,
        pnl                 REAL,             -- realized P&L in dollars
        status              TEXT DEFAULT 'open',  -- 'open' | 'closed' | 'cancelled'
        composite_score     REAL,             -- final_score at trade entry
        signal_breakdown    TEXT,             -- JSON: {ta_score, sentiment_score, speed_score}
        slippage            REAL DEFAULT 0,   -- slippage applied (fraction)
        mode                TEXT NOT NULL     -- 'paper' | 'live'
    )
    """,

    # ------------------------------------------------------------------ #
    # 6. Positions — currently open positions (subset of trades)
    # ------------------------------------------------------------------ #
    """
    CREATE TABLE IF NOT EXISTS positions (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_id        INTEGER NOT NULL REFERENCES trades(id),
        market_id       TEXT NOT NULL REFERENCES markets(id),
        direction       TEXT NOT NULL,
        quantity        REAL NOT NULL,
        entry_price     REAL NOT NULL,
        current_price   REAL,                 -- updated by market price feeds
        unrealized_pnl  REAL DEFAULT 0,
        last_updated    TEXT DEFAULT (datetime('now'))
    )
    """,

    # ------------------------------------------------------------------ #
    # 7. Balance history — periodic snapshots for equity curve
    # ------------------------------------------------------------------ #
    """
    CREATE TABLE IF NOT EXISTS balance_history (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        balance     REAL NOT NULL,
        mode        TEXT NOT NULL,            -- 'paper' | 'live'
        timestamp   TEXT DEFAULT (datetime('now'))
    )
    """,

    # ------------------------------------------------------------------ #
    # 8. Agent log — weight adjustments and strategy changes
    # ------------------------------------------------------------------ #
    """
    CREATE TABLE IF NOT EXISTS agent_log (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        action      TEXT NOT NULL,            -- e.g. 'weight_adjustment'
        category    TEXT,                     -- 'sports' | 'crypto' | 'weather'
        old_value   TEXT,                     -- JSON: previous weights
        new_value   TEXT,                     -- JSON: new weights
        reason      TEXT,                     -- human-readable explanation
        timestamp   TEXT DEFAULT (datetime('now'))
    )
    """,

    # ------------------------------------------------------------------ #
    # 9. Data source status — health monitoring for all external APIs
    # ------------------------------------------------------------------ #
    """
    CREATE TABLE IF NOT EXISTS data_source_status (
        id          TEXT PRIMARY KEY,         -- e.g. 'kalshi_rest', 'openweather'
        source_name TEXT NOT NULL,
        status      TEXT DEFAULT 'unknown',   -- 'healthy' | 'degraded' | 'down' | 'unknown'
        last_success TEXT,
        last_error  TEXT,
        error_count INTEGER DEFAULT 0,
        latency_ms  REAL
    )
    """,

    # ------------------------------------------------------------------ #
    # 10. Sentiment cache — cached NLP results to avoid redundant calls
    # ------------------------------------------------------------------ #
    """
    CREATE TABLE IF NOT EXISTS sentiment_cache (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        source          TEXT NOT NULL,        -- 'newsapi' | 'reddit' | etc.
        query           TEXT NOT NULL,        -- the search query used
        sentiment_score REAL NOT NULL,        -- 0-100
        raw_text        TEXT,                 -- stored headlines/text
        timestamp       TEXT DEFAULT (datetime('now'))
    )
    """,

    # ------------------------------------------------------------------ #
    # 11. Bot log — timestamped log of every bot action
    # ------------------------------------------------------------------ #
    """
    CREATE TABLE IF NOT EXISTS bot_log (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        level       TEXT NOT NULL DEFAULT 'INFO',  -- 'DEBUG'|'INFO'|'WARNING'|'ERROR'
        module      TEXT,                          -- source module name
        message     TEXT NOT NULL,
        timestamp   TEXT DEFAULT (datetime('now'))
    )
    """,

    # ------------------------------------------------------------------ #
    # 12. Settings — key-value store for runtime configuration
    # ------------------------------------------------------------------ #
    """
    CREATE TABLE IF NOT EXISTS settings (
        key         TEXT PRIMARY KEY,
        value       TEXT NOT NULL,
        description TEXT,
        updated_at  TEXT DEFAULT (datetime('now'))
    )
    """,

    # ------------------------------------------------------------------ #
    # 13. Strategy weights — current and historical signal weights
    # ------------------------------------------------------------------ #
    """
    CREATE TABLE IF NOT EXISTS strategy_weights (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        category            TEXT NOT NULL,    -- 'sports' | 'crypto' | 'weather'
        ta_weight           REAL NOT NULL,
        sentiment_weight    REAL NOT NULL,
        speed_weight        REAL NOT NULL,
        performance_score   REAL,             -- rolling win rate used to decide adjustments
        updated_at          TEXT DEFAULT (datetime('now'))
    )
    """,
]

# Indices for frequently-queried columns
_INDEX_STATEMENTS = [
    "CREATE INDEX IF NOT EXISTS idx_candlesticks_market_ts ON candlesticks(market_id, timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_signals_market ON signals(market_id)",
    "CREATE INDEX IF NOT EXISTS idx_signals_type ON signals(signal_type)",
    "CREATE INDEX IF NOT EXISTS idx_signals_ts ON signals(timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_composite_scores_market ON composite_scores(market_id)",
    "CREATE INDEX IF NOT EXISTS idx_trades_market ON trades(market_id)",
    "CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status)",
    "CREATE INDEX IF NOT EXISTS idx_trades_mode ON trades(mode)",
    "CREATE INDEX IF NOT EXISTS idx_positions_market ON positions(market_id)",
    "CREATE INDEX IF NOT EXISTS idx_bot_log_ts ON bot_log(timestamp)",
    "CREATE INDEX IF NOT EXISTS idx_bot_log_level ON bot_log(level)",
    "CREATE INDEX IF NOT EXISTS idx_strategy_weights_cat ON strategy_weights(category)",
]


def create_all_tables() -> None:
    """
    Create all database tables and indices if they don't already exist.
    Safe to call multiple times (uses IF NOT EXISTS).
    Call once at application startup.
    """
    with get_db() as conn:
        for stmt in _SCHEMA_STATEMENTS:
            conn.execute(stmt)
        for stmt in _INDEX_STATEMENTS:
            conn.execute(stmt)
    _seed_default_settings()
    _seed_default_weights()


def _seed_default_settings() -> None:
    """Insert default settings if the settings table is empty."""
    from database.connection import execute_query
    existing = execute_query("SELECT COUNT(*) as cnt FROM settings")
    if existing and existing[0]["cnt"] > 0:
        return

    defaults = [
        ("trade_threshold", "65", "Minimum composite score to enter a trade"),
        ("max_positions", "5", "Maximum concurrent open positions"),
        ("max_exposure_per_trade", "0.20", "Max fraction of balance per single trade"),
        ("max_total_exposure", "0.80", "Max fraction of balance across all positions"),
        ("stop_loss_pct", "0.15", "Stop loss at X% below entry"),
        ("take_profit_pct", "0.30", "Take profit at X% above entry"),
        ("scan_interval_sec", "30", "Market scan loop interval in seconds"),
        ("sentiment_model", "vader", "NLP model: vader or finbert"),
    ]

    with get_db() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO settings (key, value, description) VALUES (?, ?, ?)",
            defaults,
        )


def _seed_default_weights() -> None:
    """Insert default strategy weights if the table is empty."""
    from database.connection import execute_query
    existing = execute_query("SELECT COUNT(*) as cnt FROM strategy_weights")
    if existing and existing[0]["cnt"] > 0:
        return

    defaults = [
        ("sports",  0.20, 0.35, 0.45),
        ("crypto",  0.40, 0.30, 0.30),
        ("weather", 0.15, 0.05, 0.80),
    ]

    with get_db() as conn:
        conn.executemany(
            """INSERT OR IGNORE INTO strategy_weights
               (category, ta_weight, sentiment_weight, speed_weight)
               VALUES (?, ?, ?, ?)""",
            defaults,
        )


def get_current_weights(category: str) -> dict:
    """
    Return the most recently set strategy weights for a category.
    Falls back to hardcoded defaults if no DB record exists.
    """
    from database.connection import execute_query
    from config import config

    rows = execute_query(
        """SELECT ta_weight, sentiment_weight, speed_weight
           FROM strategy_weights
           WHERE category = ?
           ORDER BY updated_at DESC
           LIMIT 1""",
        (category.lower(),),
    )

    if rows:
        row = rows[0]
        return {
            "ta": row["ta_weight"],
            "sentiment": row["sentiment_weight"],
            "speed": row["speed_weight"],
        }

    # Fall back to config defaults
    wc = config.get_weights(category)
    return wc.as_dict()
