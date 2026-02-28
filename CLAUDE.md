# CLAUDE.md — Prediction Market Trading Bot

> **Read this file before every session. Update it as the project evolves.**
> This is the single source of truth for the project's architecture, decisions, and status.

## Project Overview

An automated prediction market trading bot with a full-featured dashboard that monitors, analyzes, and trades contracts on **Kalshi** and **Polymarket**. The bot combines three signal types — **technical analysis on prediction market chart data**, **sentiment analysis from news and social media**, and **speed advantage on breaking information** — to find edge and execute trades autonomously. It focuses exclusively on three market categories: **sports outcomes**, **Bitcoin/Ethereum prices**, and **weather**.

The bot has **agentic properties** — it can develop strategies, monitor its own performance, and make adjustments to its approach over time based on what's working and what isn't. It is not a static rules engine; it learns and adapts.

The dashboard gives the user full visibility and manual override control over everything the bot does. No black boxes.

This bot is designed to run 24/7 on a **dedicated Mac Mini (2018, 8GB RAM, 512GB SSD)** with an isolated security setup (anonymous Apple ID, dedicated non-admin user account, Mullvad VPN). It may integrate with or run alongside **OpenClaw** as an agent framework, with custom Python modules extending its capabilities.

## Problem Statement

Prediction markets on Kalshi and Polymarket are still relatively new and inefficient. The window of opportunity for automated edge is closing as more sophisticated participants enter. This bot moves fast by combining multiple signal types that most manual traders can't process simultaneously — real-time chart analysis, sentiment from dozens of sources, and speed on breaking news — to identify mispricings and trade them before the market corrects.

## Core Principles

### Principle 1: The User Sees Everything
The dashboard is the command center. Every decision the bot makes, every trade it enters, every signal it detects, every data source it's pulling from — all of it must be visible, explainable, and controllable from the dashboard. The user should be able to understand *why* the bot made every decision.

### Principle 2: Paper Trading First, Always
The bot must launch in paper trading mode by default with a **$100 simulated bankroll** (matching the planned real capital). Live trading requires an explicit toggle with a confirmation dialog. Paper trading simulates all trades against real market data with realistic slippage simulation. The bot must prove consistent profitability in paper mode before going live.

### Principle 3: Three Signal Types Working Together
The bot does NOT rely on any single signal type. Every trading decision is informed by a weighted combination of:
1. **Technical Analysis (TA)** — chart patterns, indicators, volume, and orderbook analysis on the prediction market price data itself
2. **Sentiment Analysis** — aggregated sentiment scoring from news sources, social media, and domain-specific publications
3. **Speed/Information Advantage** — being faster than the market to price in breaking news, injury reports, weather updates, exchange data, etc.

Each signal type produces a confidence score. The final trading decision weights all three.

### Principle 4: Agentic Self-Improvement
The bot monitors its own performance and adjusts strategy weights over time. If TA signals are outperforming sentiment signals in crypto markets, it should increase TA weight for crypto. If speed signals are killing it in sports but losing in weather, it adjusts. This feedback loop runs on a configurable schedule (default: every 20 trades per category).

### Principle 5: Focused Market Categories
The bot ONLY trades in three categories:
- **Sports outcomes** — game results, point spreads, player props
- **Bitcoin/Ethereum prices** — will BTC/ETH be above/below X by date Y
- **Weather** — temperature thresholds, precipitation, storm events

Do not scan or trade politics, entertainment, economics, or any other category.

### Principle 6: Live Market Data by Default
Always assume live price data is needed. All prediction market data comes from the Kalshi and Polymarket APIs directly. Underlying asset data (crypto prices, weather, sports stats) comes from multiple external sources for redundancy and consensus. Cache aggressively but never serve stale data for trading decisions.

### Principle 7: Security-First Development
- All secrets in `.env` — never hardcoded. `.env.example` committed to git.
- Parameterized queries only — no SQL string concatenation.
- Never log or display API keys, private keys, or wallet addresses.
- `web3==6.14.0` PINNED — breaking change in 7.x breaks py-clob-client.
- Pin all dependency versions in `requirements.txt`.
- Default to paper mode — live trading requires explicit opt-in.

### Principle 8: Full Codebase Awareness Before Every Change
Read every relevant file before making changes. Check DB schema. Know installed packages. Read all config files. Investigate before acting.

### Principle 9: Professional-Grade User Interface
- Dark mode terminal UI via Textual
- Color with purpose: Green=profit/healthy, Red=loss/error, Blue=TA signals, Purple=sentiment signals, Orange=speed signals, Cyan=agent actions, Yellow=warnings, Gray=secondary
- All 9 tabs must be keyboard-navigable (press 1-9)
- Real-time updates: positions refresh every 1s, markets every 5s, feeds every 30s

### Principle 10: Update This File
Update CLAUDE.md as the project progresses: check off completed items, document API quirks, record architectural decisions, note performance findings.

## Technical Architecture

### Language & Framework
- **Python 3.11+** — primary language (running 3.14.2 on dev machine)
- **Textual** — terminal UI framework for the dashboard
- **SQLite** — local data storage (WAL mode for concurrent reads/writes)
- **asyncio + asyncio.TaskGroup** — concurrent market monitoring (Python 3.11+)
- **aiohttp** — async HTTP for REST API calls
- **websockets** — real-time data streams

### CRITICAL: Python Version and Dependency Setup

**Requires Python 3.13.** The project uses a `.venv` at the project root.

```bash
# One-time setup
python3.13 -m venv .venv
source .venv/bin/activate

# Install web3 without its ckzg pin (ckzg 2.x ships with py-clob-client)
pip install web3==6.14.0 --no-deps
pip install eth-abi eth-account "eth-hash[pycryptodome]" eth-typing \
            eth-utils hexbytes jsonschema "lru-dict==1.2.0" \
            pyunormalize requests websockets aiohttp protobuf
pip install py-clob-client    # installs ckzg 2.x (has Py3.13 wheels)

# Install the rest of the project dependencies
pip install -r requirements.txt

# Remove web3's broken pytest plugin (incompatible with eth-typing 5.x)
sed -i '' '/pytest_ethereum/d' \
  .venv/lib/python3.13/site-packages/web3-6.14.0.dist-info/entry_points.txt
```

**Why Python 3.13?** web3==6.14.0 pins `ckzg` to a version with no Python 3.14
wheels. py-clob-client (Polymarket SDK) installs `ckzg 2.x` which has Py3.13
wheels and is fully compatible. web3 7.x breaks py-clob-client's ABI encoding
— never upgrade beyond 6.x without testing py-clob-client compatibility first.

### How to Run
```bash
cd "/Users/jacobjohanson/Prediction Bot"
source .venv/bin/activate
cp .env.example .env   # fill in API keys (all optional for paper mode)
python main.py
# Press 1-9 to navigate tabs, q to quit, p for panic close all
```

### Project Structure
```
prediction-bot/
├── CLAUDE.md                  # This file — project guide
├── .env.example               # Template for environment variables (committed)
├── .env                       # Actual secrets (gitignored)
├── .gitignore
├── requirements.txt
├── main.py                    # Entry point — TradingEngine + async TaskGroup
├── config.py                  # Config singleton (loaded from .env)
├── database/
│   ├── __init__.py
│   ├── connection.py          # SQLite connection manager (WAL mode)
│   ├── schema.py              # CREATE TABLE statements (13 tables)
│   └── models.py              # Python dataclasses for DB rows
├── exchanges/
│   ├── __init__.py
│   ├── kalshi.py              # Kalshi REST + WebSocket (RSA auth)
│   └── polymarket.py          # Polymarket CLOB (py-clob-client wrapper)
├── data_sources/
│   ├── __init__.py
│   ├── crypto.py              # Binance public API (no auth needed)
│   ├── weather.py             # OpenWeatherMap API
│   ├── sports.py              # The Odds API
│   └── news.py                # NewsAPI headlines
├── analysis/
│   ├── __init__.py
│   ├── technical.py           # TA engine + double-breakout state machine
│   ├── sentiment.py           # VADER-based NLP sentiment scoring
│   └── speed.py               # Speed/information advantage monitor
├── engine/
│   ├── __init__.py
│   ├── risk.py                # Position sizing + exposure limits
│   ├── signals.py             # Composite score aggregation
│   ├── paper_trading.py       # Paper trade execution with slippage
│   └── agent.py               # Rule-based weight adjustment (no LLM)
├── dashboard/
│   ├── __init__.py
│   ├── app.py                 # TradingBotApp (Textual root)
│   └── tabs/
│       ├── __init__.py
│       ├── overview.py        # Tab 1: balance, mode, P&L, equity curve
│       ├── active_markets.py  # Tab 2: market scanner with signals
│       ├── trade_history.py   # Tab 3: full trade log
│       ├── active_positions.py# Tab 4: open positions + panic button
│       ├── signal_log.py      # Tab 5: all signals from all 3 types
│       ├── data_feeds.py      # Tab 6: API health status board
│       ├── agent_insights.py  # Tab 7: weight history + performance
│       ├── settings.py        # Tab 8: all configuration
│       └── bot_activity.py    # Tab 9: real-time scrolling log
├── notifications/
│   ├── __init__.py
│   └── telegram.py            # Telegram bot (silent no-op if unconfigured)
└── tests/
    ├── test_technical.py
    ├── test_signals.py
    ├── test_paper_trading.py
    └── test_risk.py
```

## Database Schema (13 Tables)

### markets
```sql
CREATE TABLE IF NOT EXISTS markets (
    id TEXT PRIMARY KEY,          -- "kalshi:TICKER" or "polymarket:TOKEN_ID"
    exchange TEXT NOT NULL,       -- 'kalshi' | 'polymarket'
    ticker TEXT NOT NULL,
    category TEXT NOT NULL,       -- 'sports' | 'crypto' | 'weather'
    title TEXT NOT NULL,
    yes_price REAL DEFAULT 0.5,
    no_price REAL DEFAULT 0.5,
    volume REAL DEFAULT 0,
    open_interest REAL DEFAULT 0,
    close_date TEXT,
    status TEXT DEFAULT 'active',
    last_updated TEXT DEFAULT (datetime('now'))
);
```

### candlesticks
```sql
CREATE TABLE IF NOT EXISTS candlesticks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id TEXT NOT NULL REFERENCES markets(id),
    timestamp TEXT NOT NULL,
    open REAL, high REAL, low REAL, close REAL, volume REAL,
    UNIQUE(market_id, timestamp)
);
```

### signals
```sql
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id TEXT NOT NULL REFERENCES markets(id),
    signal_type TEXT NOT NULL,    -- 'ta' | 'sentiment' | 'speed'
    signal_name TEXT NOT NULL,
    value REAL NOT NULL,
    direction TEXT,               -- 'bullish' | 'bearish' | 'neutral'
    confidence REAL,
    metadata TEXT,                -- JSON blob
    timestamp TEXT DEFAULT (datetime('now'))
);
```

### composite_scores
```sql
CREATE TABLE IF NOT EXISTS composite_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id TEXT NOT NULL REFERENCES markets(id),
    ta_score REAL, sentiment_score REAL, speed_score REAL,
    ta_weight REAL, sentiment_weight REAL, speed_weight REAL,
    final_score REAL NOT NULL,
    recommendation TEXT,          -- 'BUY_YES' | 'BUY_NO' | 'HOLD'
    timestamp TEXT DEFAULT (datetime('now'))
);
```

### trades
```sql
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id TEXT NOT NULL REFERENCES markets(id),
    exchange TEXT NOT NULL,
    direction TEXT NOT NULL,      -- 'YES' | 'NO'
    quantity REAL NOT NULL,
    entry_price REAL NOT NULL,
    exit_price REAL,
    entry_time TEXT NOT NULL,
    exit_time TEXT,
    pnl REAL,
    status TEXT DEFAULT 'open',   -- 'open' | 'closed' | 'cancelled'
    composite_score REAL,
    signal_breakdown TEXT,        -- JSON: {ta_score, sentiment_score, speed_score}
    slippage REAL DEFAULT 0,
    mode TEXT NOT NULL            -- 'paper' | 'live'
);
```

### positions
```sql
CREATE TABLE IF NOT EXISTS positions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id INTEGER REFERENCES trades(id),
    market_id TEXT NOT NULL REFERENCES markets(id),
    direction TEXT NOT NULL,
    quantity REAL NOT NULL,
    entry_price REAL NOT NULL,
    current_price REAL,
    unrealized_pnl REAL DEFAULT 0,
    last_updated TEXT DEFAULT (datetime('now'))
);
```

### balance_history
```sql
CREATE TABLE IF NOT EXISTS balance_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    balance REAL NOT NULL,
    mode TEXT NOT NULL,
    timestamp TEXT DEFAULT (datetime('now'))
);
```

### agent_log
```sql
CREATE TABLE IF NOT EXISTS agent_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    category TEXT,
    old_value TEXT,               -- JSON
    new_value TEXT,               -- JSON
    reason TEXT,
    timestamp TEXT DEFAULT (datetime('now'))
);
```

### data_source_status
```sql
CREATE TABLE IF NOT EXISTS data_source_status (
    id TEXT PRIMARY KEY,
    source_name TEXT NOT NULL,
    status TEXT DEFAULT 'unknown', -- 'healthy' | 'degraded' | 'down' | 'unknown'
    last_success TEXT,
    last_error TEXT,
    error_count INTEGER DEFAULT 0,
    latency_ms REAL
);
```

### sentiment_cache
```sql
CREATE TABLE IF NOT EXISTS sentiment_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    query TEXT NOT NULL,
    sentiment_score REAL NOT NULL,
    raw_text TEXT,
    timestamp TEXT DEFAULT (datetime('now'))
);
```

### bot_log
```sql
CREATE TABLE IF NOT EXISTS bot_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    level TEXT NOT NULL,          -- 'DEBUG' | 'INFO' | 'WARNING' | 'ERROR'
    module TEXT,
    message TEXT NOT NULL,
    timestamp TEXT DEFAULT (datetime('now'))
);
```

### settings
```sql
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    description TEXT,
    updated_at TEXT DEFAULT (datetime('now'))
);
```

### strategy_weights
```sql
CREATE TABLE IF NOT EXISTS strategy_weights (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT NOT NULL,
    ta_weight REAL NOT NULL,
    sentiment_weight REAL NOT NULL,
    speed_weight REAL NOT NULL,
    performance_score REAL,
    updated_at TEXT DEFAULT (datetime('now'))
);
```

## External APIs & Services

### Prediction Market APIs

#### Kalshi API
- **Base URL:** `https://api.elections.kalshi.com/trade-api/v2`
- **Python SDK:** `kalshi-python` (pip install kalshi-python)
- **Auth:** API key ID + PEM private key (RSA signing)
- **Signature:** `base64(RSA_PKCS1v15_SHA256(f"{timestamp_ms}{METHOD}/trade-api/v2{path}"))`
- **Headers:** `KALSHI-ACCESS-KEY`, `KALSHI-ACCESS-SIGNATURE`, `KALSHI-ACCESS-TIMESTAMP` (ms)
- **Key endpoints:**
  - `GET /markets` — list/filter markets by status, series, category (public, no auth)
  - `GET /markets/{ticker}` — single market details
  - `GET /series/{series_ticker}/markets/{ticker}/candlesticks` — OHLCV (period: 1=1min, 60=1hr, 1440=1day)
  - `GET /markets/candlesticks` — batch up to 10,000 candles across multiple markets
  - `GET /markets/{ticker}/orderbook` — orderbook depth
  - `GET /portfolio/balance` — account balance (auth required)
  - `GET /portfolio/positions` — open positions (auth required)
  - `POST /portfolio/orders` — place orders (auth required)
  - WebSocket: `wss://api.elections.kalshi.com/trade-api/ws/v2`
- **Note:** Orderbook only returns bids; YES bid at 60¢ implies NO ask at 40¢

#### Polymarket API
- **CLOB API:** `https://clob.polymarket.com`
- **Python SDK:** `py-clob-client` — ALL METHODS ARE SYNCHRONOUS, use run_in_executor
- **Auth:** Polygon wallet private key, chain_id=137
- **web3 MUST be pinned to 6.14.0** — never upgrade to 7.x
- **Key methods:** `get_markets()`, `get_order_book(token_id)`, `get_midpoint(token_id)`, `create_order()`, `post_order()`
- **Gamma API** (`https://gamma-api.polymarket.com`) — market discovery and metadata
- **US persons restricted** from trading per ToS — read-only market data globally permitted

### Crypto Price Data
- **Binance** — `https://api.binance.com/api/v3/klines` — public, no auth, BTC/ETH OHLCV
- **CoinGecko** — fear/greed, market sentiment indicators

### Weather Data
- **OpenWeatherMap** — free tier 60 calls/min, current + forecast
- **NOAA/Weather.gov** — free, no key, authoritative US weather

### Sports Data
- **The Odds API** — free tier 500 req/month, aggregated sportsbook odds

### News & Sentiment
- **NewsAPI** — free tier 100 req/day, aggregated headlines

### Notifications
- **Telegram Bot API** — push notifications for trades and alerts

## Technical Analysis Strategy (Signal Type 1: TA)

### Double-Breakout State Machine (PRIMARY SIGNAL)
States: `SCANNING → CONSOLIDATION_DETECTED → FIRST_BREAKOUT → RETEST → SECOND_BREAKOUT_SIGNAL`

Transitions:
- `SCANNING → CONSOLIDATION_DETECTED`: ≥5 candles with (high-low)/price < 3%
- `CONSOLIDATION_DETECTED → FIRST_BREAKOUT`: close > consolidation_high × 1.015 or < low × 0.985
- `FIRST_BREAKOUT → RETEST`: price returns within 1% of breakout level
- `RETEST → SECOND_BREAKOUT_SIGNAL`: breaks again in same direction with volume > average → +25 confidence
- Any → `SCANNING`: opposite breakout OR 50-candle timeout

### Indicators
- `SMA(10)` — short-term trend
- `EMA(60)` — medium-term trend
- `SMA(360)` — long-term (when enough data exists)
- `VWAP` — volume-weighted average price
- Volume spike detection (>2× 20-period average = significant)
- Orderbook imbalance (YES bids vs NO bids ratio)

### TA Scoring (0-100)
- Higher timeframe data → higher base score
- Volume confirmation on breakout → +15
- Orderbook imbalance favoring direction → +10
- Double-breakout confirmed → +25 over single breakout
- Multiple indicators agreeing → +10 per confirmation

## Sentiment Analysis Strategy (Signal Type 2)

- **VADER** for quick scoring (no model download, runs in <1ms)
- Score normalized: -1→1 VADER compound mapped to 0→100 (50=neutral)
- Sources: NewsAPI headlines, Reddit (future), RSS feeds (future)
- Weight recent more than old; detect score velocity (rapid shift = speed signal)
- **FinBERT upgrade**: optional in later session, enabled via `SENTIMENT_MODEL=finbert` in .env

### Per-Category Focus
- Sports: injury reports, team news, betting line divergence
- Crypto: coin-specific news, regulatory, macro sentiment, Fear & Greed
- Weather: extreme event coverage, forecast disagreement

## Speed/Information Advantage Strategy (Signal Type 3)

- **Freshness scoring**: <5s from update = full score; degrades with staleness
- **Volume spike**: ratio > 2× baseline = potential informed trading
- **Price momentum**: magnitude of recent price moves
- **Cross-source consensus**: if 4/5 sources agree on outcome and prediction market disagrees = edge
- **Crypto speed**: BTC/ETH move on one exchange before prediction market adjusts

## Composite Signal Aggregation

```
final_score = (ta_score × ta_weight) + (sentiment_score × sentiment_weight) + (speed_score × speed_weight)
```

Default weights by category:
- **Sports:** TA=0.20, Sentiment=0.35, Speed=0.45
- **Crypto:** TA=0.40, Sentiment=0.30, Speed=0.30
- **Weather:** TA=0.15, Sentiment=0.05, Speed=0.80

Trade only when: `final_score ≥ 65` AND at least 2 of 3 signal types agree on direction.

## Entry & Exit Rules
- Entry: composite ≥ 65, ≥2/3 signal types agree
- Position sizing: Kelly-inspired — score 65→5% of balance, score 100→20% of balance
- Stop loss: 15% of position value (configurable)
- Take profit: 30% (configurable)
- Max concurrent positions: 5 (configurable)
- Max per-trade exposure: 20% of balance (configurable)
- Max total exposure: 80% of balance (configurable)
- Always keep ≥20% cash

## Agentic Self-Improvement
- Evaluate every 20 closed trades per category
- Signal "accurate" if directional call matched trade outcome
- Accuracy >65% → increase that signal's weight by 0.05
- Accuracy <40% → decrease by 0.05
- Renormalize weights to sum to 1.0 after adjustment
- Bounds: MIN=0.05, MAX=0.70
- Log every adjustment to `agent_log` with full reasoning
- Cannot change entry/exit rules without user approval

## Dashboard — 9 Tabs

| # | Tab | Key Content | Refresh |
|---|-----|-------------|---------|
| 1 | Overview | Balance, mode badge (PAPER/LIVE), P&L, equity curve, quick stats | 2s |
| 2 | Active Markets | DataTable of monitored markets with TA/Sentiment/Speed badges | 5s |
| 3 | Trade History | Full trade log, per-trade signal breakdown, CSV export | 10s |
| 4 | Active Positions | Live P&L, Close buttons, PANIC CLOSE ALL | 1s |
| 5 | Signal Log | Scrolling colored log of all detected signals | 3s |
| 6 | Data Feeds | Health status of all external APIs | 30s |
| 7 | Agent Insights | Weight history, performance by signal type | 30s |
| 8 | Settings | All parameters, API key management, mode toggle | on-demand |
| 9 | Bot Activity | Real-time log of all bot actions | streaming |

Keyboard: press `1-9` to switch tabs, `q` to quit, `p` for panic close all.

## Environment Variables (.env)

```bash
# === TRADING MODE ===
TRADING_MODE=paper              # 'paper' or 'live'
PAPER_STARTING_BALANCE=100.0

# === KALSHI ===
KALSHI_API_KEY_ID=your-api-key-id-here
KALSHI_PRIVATE_KEY_PATH=./kalshi_private_key.pem

# === POLYMARKET ===
POLYMARKET_PRIVATE_KEY=your-polygon-wallet-private-key-here
POLYMARKET_FUNDER_ADDRESS=your-funder-address-here

# === CRYPTO EXCHANGES ===
BINANCE_API_KEY=your-key
BINANCE_API_SECRET=your-secret
COINGECKO_API_KEY=your-key

# === WEATHER ===
OPENWEATHERMAP_API_KEY=your-key

# === SPORTS ===
THE_ODDS_API_KEY=your-key
SPORTSRADAR_API_KEY=your-key

# === NEWS & SENTIMENT ===
NEWS_API_KEY=your-key
REDDIT_CLIENT_ID=your-client-id
REDDIT_CLIENT_SECRET=your-client-secret

# === NOTIFICATIONS ===
TELEGRAM_BOT_TOKEN=your-bot-token
TELEGRAM_CHAT_ID=your-chat-id

# === RISK PARAMETERS ===
MAX_POSITIONS=5
MAX_EXPOSURE_PER_TRADE=0.20
MAX_TOTAL_EXPOSURE=0.80
TRADE_THRESHOLD=65

# === OPTIONAL ===
SENTIMENT_MODEL=vader           # 'vader' or 'finbert'
LOG_LEVEL=INFO
```

## Development Workflow
1. Build database schema and models first
2. Build exchange client wrappers with error handling and stub mode
3. Build data source clients — start with free/no-auth tiers
4. Build TA engine and apply to candlestick data
5. Build sentiment analysis pipeline
6. Build speed/information advantage monitors
7. Build composite signal aggregation
8. Build paper trading engine
9. Build dashboard shell with all 9 tabs
10. Build agentic self-improvement layer
11. Wire everything together in the main async engine loop
12. Test extensively in paper mode — minimum 2 weeks before live
13. Add live trading capability last
14. Build notification system

## Development Workflow Expectations
1. **Read CLAUDE.md first.** Every time. Before touching any code.
2. **Understand current state** before making changes. Read relevant files.
3. **Make focused, well-commented changes.** Every function gets a docstring. Every module gets a module-level docstring.
4. **Test after every meaningful change.** Prove it works, don't assume.
5. **Explain what was done and why** after making changes.
6. **Ask before architectural decisions.** Present options with tradeoffs.
7. **Never break existing functionality** to add new features.
8. **Handle API failures gracefully.** Stale data > blank screen > crash.
9. **Cache aggressively**, respect rate limits, track usage per source.

## API Quirks & Edge Cases
- Kalshi orderbook only returns bids (binary market structure) — YES bid at X implies NO ask at 1-X
- Polymarket prices are 0.00-1.00 (not 0-100 cents) — be careful with arithmetic
- Binance klines return strings for OHLCV — must cast to float
- py-clob-client methods are SYNCHRONOUS — always use run_in_executor
- Textual `run_async()` must run in same event loop as other async tasks — use TaskGroup

## Current Status

### Phase 1 — Paper Trading MVP ✓ COMPLETE
- [x] Project scaffolding and CLAUDE.md created
- [x] Database schema (13 tables) and connection manager
- [x] Config singleton (paper mode default, $100 balance)
- [x] Kalshi API client (stub mode complete, real RSA auth wired)
- [x] Polymarket API client (stub mode complete, py-clob-client wired)
- [x] Crypto data source (Binance public klines)
- [x] Weather data source (OpenWeatherMap)
- [x] Sports data source (The Odds API)
- [x] News/sentiment source (NewsAPI + VADER)
- [x] Technical analysis engine (indicators + double-breakout state machine)
- [x] Sentiment analysis pipeline (VADER + domain boosters)
- [x] Speed/information advantage monitor
- [x] Composite signal aggregation and scoring
- [x] Paper trading engine (slippage, position sizing, P&L)
- [x] Risk management system (Kelly sizing, max positions, exposure limits)
- [x] Dashboard — Overview tab
- [x] Dashboard — Active Markets tab
- [x] Dashboard — Trade History tab
- [x] Dashboard — Active Positions tab (with panic button)
- [x] Dashboard — Signal Log tab
- [x] Dashboard — Data Feeds Status tab
- [x] Dashboard — Agent Insights tab
- [x] Dashboard — Settings tab
- [x] Dashboard — Bot Activity Log tab
- [x] main.py entry point (TradingEngine + asyncio.TaskGroup)
- [x] Agentic self-improvement system (rule-based weight adjustment)
- [x] Telegram notification system (silent no-op when unconfigured)
- [x] Testing suite (34 tests, all passing)

### Phase 2 — Real API Connections (Next)
- [ ] Kalshi WebSocket live orderbook streaming
- [ ] Polymarket Polygon wallet auth + real order placement
- [ ] Binance WebSocket sub-second price feed
- [ ] Real OpenWeatherMap, The Odds API, NewsAPI connections

### Phase 3 — Live Trading
- [ ] Live trading toggle (Settings tab confirmation dialog)
- [ ] Real Kalshi order placement (RSA-signed POST)
- [ ] Real Polymarket order placement (py-clob-client)
- [ ] BUILD.md with setup/run instructions

## Deployment Notes
- Runs on dedicated Mac Mini (2018, 8GB RAM, 512GB SSD, macOS Sequoia)
- Isolated security: anonymous Apple ID, dedicated non-admin macOS user, Mullvad VPN
- Designed for 24/7 autonomous operation
- SSH accessible for remote dashboard viewing via Textual
- May run alongside or integrate with OpenClaw agent framework

## Session Log
- **2026-02-23** — Initial project created. Full CLAUDE.md written. Beginning Phase 1 build (foundation + paper trading MVP).
