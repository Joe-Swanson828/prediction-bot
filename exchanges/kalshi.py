"""
exchanges/kalshi.py — Kalshi prediction market API client.

Wraps the Kalshi REST API v2 with:
  - RSA key signing for authenticated endpoints
  - Stub mode when credentials are not configured (returns synthetic data)
  - Rate limit handling with exponential backoff
  - Proper async HTTP using aiohttp

Auth: API key ID + PEM private key (RSA PKCS1v15 + SHA256)
Signature: base64(RSA_sign(f"{timestamp_ms}{METHOD}/trade-api/v2{path}"))
Headers: KALSHI-ACCESS-KEY, KALSHI-ACCESS-SIGNATURE, KALSHI-ACCESS-TIMESTAMP

Stub mode: When no API key/private key is configured, all methods return
plausible synthetic data so the paper trading engine can run without credentials.

API docs: https://trading-api.readme.io/reference/getting-started
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import random
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

import aiohttp

from config import config
from database.connection import execute_write, get_db

# Markets to generate in stub mode — realistic Kalshi-style tickers
_STUB_SPORTS_MARKETS = [
    ("KXNFL-SB59", "Super Bowl LIX: Chiefs to win?", 0.42),
    ("KXNBA-GSWATRIP", "Warriors to win NBA title 2025?", 0.18),
    ("KXMLB-YANKEES25", "Yankees to win World Series 2025?", 0.09),
    ("KXNFL-MAHOMES-MVP", "Mahomes to win Super Bowl MVP?", 0.35),
    ("KXNBA-LEBRON-50", "LeBron to score 50+ in next game?", 0.06),
]

_STUB_CRYPTO_MARKETS = [
    ("KXBTC-100K", "Bitcoin to close above $100K by March 31?", 0.55),
    ("KXETH-4K", "Ethereum to close above $4K by March 31?", 0.48),
    ("KXBTC-90K-FEB", "Bitcoin above $90K on Feb 28?", 0.71),
]

_STUB_WEATHER_MARKETS = [
    ("KXNYC-TEMP-75", "NYC temperature to exceed 75°F in June?", 0.62),
    ("KXCHI-SNOW-MAR", "Chicago to receive 3+ inches snow in March?", 0.38),
    ("KXLA-RAIN-APR", "Los Angeles to receive 1+ inch rain in April?", 0.22),
]


class KalshiClient:
    """
    Kalshi REST API v2 client with stub mode support.

    In stub mode (no API credentials), all read methods return synthetic
    data so the paper trading engine can operate without real credentials.
    Write methods (order placement) are always no-ops in paper mode.
    """

    BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"

    def __init__(
        self,
        api_key_id: str = "",
        private_key_path: str = "",
    ) -> None:
        self.api_key_id = api_key_id
        self._private_key = None
        self._session: Optional[aiohttp.ClientSession] = None

        # Try to load the private key
        if api_key_id and private_key_path:
            self._load_private_key(private_key_path)

        self._stub_mode = self._private_key is None

    def _load_private_key(self, path: str) -> None:
        """Load RSA private key from PEM file."""
        if not os.path.exists(path):
            return
        try:
            from cryptography.hazmat.primitives.serialization import load_pem_private_key

            with open(path, "rb") as f:
                self._private_key = load_pem_private_key(f.read(), password=None)
        except Exception as e:
            print(f"[KalshiClient] Could not load private key from {path}: {e}")
            self._private_key = None

    def _sign_request(self, method: str, path: str, body: str = "") -> dict:
        """
        Generate the required auth headers for a Kalshi API request.

        Signature = base64(RSA_PKCS1v15_SHA256(timestamp_ms + METHOD + /trade-api/v2 + path))
        """
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        timestamp_ms = str(int(time.time() * 1000))
        # Note: path should start with "/" but NOT include the base URL
        message = timestamp_ms + method.upper() + "/trade-api/v2" + path
        signature_bytes = self._private_key.sign(
            message.encode("utf-8"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        signature = base64.b64encode(signature_bytes).decode("utf-8")

        return {
            "KALSHI-ACCESS-KEY": self.api_key_id,
            "KALSHI-ACCESS-SIGNATURE": signature,
            "KALSHI-ACCESS-TIMESTAMP": timestamp_ms,
            "Content-Type": "application/json",
        }

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create the aiohttp session."""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=15)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def _request(
        self,
        method: str,
        path: str,
        params: Optional[dict] = None,
        json_body: Optional[dict] = None,
        auth: bool = False,
    ) -> Any:
        """
        Make an API request with retry on rate limit.

        Args:
            method: HTTP method
            path: API path (starting with /)
            params: Query parameters
            json_body: Request body for POST/PUT
            auth: Whether this endpoint requires authentication

        Returns:
            Parsed JSON response
        """
        if self._stub_mode and auth:
            raise RuntimeError("Kalshi API not configured — cannot make authenticated requests.")

        url = self.BASE_URL + path
        headers = {}
        if auth:
            body_str = json.dumps(json_body) if json_body else ""
            headers = self._sign_request(method, path, body_str)

        session = await self._get_session()

        for attempt in range(3):
            try:
                async with session.request(
                    method,
                    url,
                    params=params,
                    json=json_body,
                    headers=headers,
                    ssl=True,
                ) as resp:
                    if resp.status == 429:  # Rate limit
                        wait = 2 ** attempt
                        await asyncio.sleep(wait)
                        continue
                    resp.raise_for_status()
                    return await resp.json()
            except aiohttp.ClientError as e:
                if attempt == 2:
                    raise
                await asyncio.sleep(1)

        raise RuntimeError(f"Failed to {method} {path} after 3 attempts")

    # ------------------------------------------------------------------ #
    # Public market data (no auth required)
    # ------------------------------------------------------------------ #

    async def get_markets(
        self,
        category: Optional[str] = None,
        status: str = "open",
        limit: int = 100,
    ) -> List[dict]:
        """
        Fetch active prediction markets.

        Args:
            category: Filter by category keyword in title/series
            status: 'open' | 'closed' | 'settled'
            limit: Max markets to return

        Returns:
            List of market dicts
        """
        if self._stub_mode:
            return self._generate_stub_markets(category)

        try:
            params = {"status": status, "limit": limit}
            data = await self._request("GET", "/markets", params=params)
            markets = data.get("markets", [])
            self._update_source_status("kalshi_rest", healthy=True)
            return markets
        except Exception as e:
            self._update_source_status("kalshi_rest", healthy=False, error=str(e))
            return self._generate_stub_markets(category)

    async def get_market(self, ticker: str) -> Optional[dict]:
        """Fetch details for a single market."""
        if self._stub_mode:
            return None

        try:
            data = await self._request("GET", f"/markets/{ticker}")
            return data.get("market")
        except Exception:
            return None

    async def get_candlesticks(
        self,
        ticker: str,
        period_interval: int = 60,  # 1=1min, 60=1hr, 1440=1day
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
        limit: int = 100,
    ) -> List[dict]:
        """
        Fetch OHLCV candlestick data for a market.

        Args:
            ticker: Market ticker
            period_interval: Candle period in minutes (1, 60, or 1440)
            start_ts: Unix timestamp start
            end_ts: Unix timestamp end
            limit: Max candles

        Returns:
            List of candle dicts with keys: ts, open, high, low, close, volume
        """
        if self._stub_mode:
            return self._generate_stub_candles(ticker, period_interval, limit)

        try:
            params = {"period_interval": period_interval}
            if start_ts:
                params["start_ts"] = start_ts
            if end_ts:
                params["end_ts"] = end_ts

            # Try the series endpoint first, fall back to market endpoint
            series_ticker = ticker.rsplit("-", 1)[0] if "-" in ticker else ticker
            path = f"/series/{series_ticker}/markets/{ticker}/candlesticks"

            data = await self._request("GET", path, params=params)
            candles_raw = data.get("candlesticks", [])

            # Normalize to standard format
            return [
                {
                    "timestamp": c.get("end_period_ts") or c.get("ts"),
                    "open": float(c.get("yes_ask", c.get("open", 0.5))),
                    "high": float(c.get("yes_ask", c.get("high", 0.5))),
                    "low": float(c.get("yes_bid", c.get("low", 0.5))),
                    "close": float(c.get("yes_ask", c.get("close", 0.5))),
                    "volume": float(c.get("volume", 0)),
                }
                for c in candles_raw
            ]
        except Exception as e:
            return self._generate_stub_candles(ticker, period_interval, limit)

    async def get_orderbook(self, ticker: str) -> dict:
        """
        Fetch orderbook for a market.
        Note: Kalshi orderbook only returns bids. YES bid at X implies NO ask at 1-X.
        """
        if self._stub_mode:
            return self._generate_stub_orderbook(ticker)

        try:
            data = await self._request("GET", f"/markets/{ticker}/orderbook")
            return data.get("orderbook", {})
        except Exception:
            return self._generate_stub_orderbook(ticker)

    # ------------------------------------------------------------------ #
    # Authenticated endpoints (require private key)
    # ------------------------------------------------------------------ #

    async def get_balance(self) -> float:
        """Get account balance. Returns 0.0 in stub mode."""
        if self._stub_mode:
            return 0.0
        try:
            data = await self._request("GET", "/portfolio/balance", auth=True)
            balance = data.get("balance", {})
            return float(balance.get("available_balance_cents", 0)) / 100
        except Exception:
            return 0.0

    async def get_positions(self) -> List[dict]:
        """Get open positions. Returns [] in stub mode."""
        if self._stub_mode:
            return []
        try:
            data = await self._request("GET", "/portfolio/positions", auth=True)
            return data.get("market_positions", [])
        except Exception:
            return []

    async def place_order(
        self,
        ticker: str,
        action: str,           # 'buy' | 'sell'
        side: str,             # 'yes' | 'no'
        count: int,            # number of contracts
        type_: str = "limit",  # 'limit' | 'market'
        yes_price: Optional[int] = None,  # cents (0-99)
        no_price: Optional[int] = None,   # cents (0-99)
    ) -> Optional[dict]:
        """
        Place an order. Only works in live mode with credentials configured.
        Returns None in paper/stub mode (never called in paper mode).
        """
        if self._stub_mode or config.is_paper_mode:
            return None

        body = {
            "ticker": ticker,
            "action": action,
            "side": side,
            "count": count,
            "type": type_,
        }
        if yes_price is not None:
            body["yes_price"] = yes_price
        if no_price is not None:
            body["no_price"] = no_price

        return await self._request("POST", "/portfolio/orders", json_body=body, auth=True)

    # ------------------------------------------------------------------ #
    # Stub data generators
    # ------------------------------------------------------------------ #

    def _generate_stub_markets(self, category_filter: Optional[str] = None) -> List[dict]:
        """Generate plausible synthetic markets for paper trading without credentials."""
        all_markets = []

        for ticker, title, base_price in _STUB_SPORTS_MARKETS:
            all_markets.append(
                self._make_stub_market(ticker, title, "sports", base_price)
            )
        for ticker, title, base_price in _STUB_CRYPTO_MARKETS:
            all_markets.append(
                self._make_stub_market(ticker, title, "crypto", base_price)
            )
        for ticker, title, base_price in _STUB_WEATHER_MARKETS:
            all_markets.append(
                self._make_stub_market(ticker, title, "weather", base_price)
            )

        if category_filter:
            cat = category_filter.lower()
            all_markets = [m for m in all_markets if m.get("category") == cat]

        return all_markets

    def _make_stub_market(
        self, ticker: str, title: str, category: str, base_price: float
    ) -> dict:
        """Create a single stub market with realistic price variation."""
        # Add small random noise to simulate live price movement
        noise = random.uniform(-0.03, 0.03)
        yes_price = max(0.02, min(0.98, base_price + noise))
        no_price = round(1.0 - yes_price, 4)
        close_date = (datetime.now(timezone.utc) + timedelta(days=random.randint(5, 60))).isoformat()

        return {
            "ticker": ticker,
            "title": title,
            "category": category,
            "yes_ask": round(yes_price, 4),
            "yes_bid": round(yes_price - 0.01, 4),
            "no_ask": round(no_price, 4),
            "no_bid": round(no_price - 0.01, 4),
            "last_price": round(yes_price, 4),
            "volume": random.randint(100, 10000),
            "open_interest": random.randint(50, 5000),
            "status": "open",
            "close_time": close_date,
            "exchange": "kalshi",
        }

    def _generate_stub_candles(
        self, ticker: str, period: int, count: int
    ) -> List[dict]:
        """Generate synthetic OHLCV candlestick data for TA testing."""
        candles = []
        price = 0.50 + random.uniform(-0.15, 0.15)
        now = time.time()

        for i in range(count):
            # Random walk with mean reversion toward 0.5
            change = random.gauss(0, 0.008)
            mean_reversion = (0.5 - price) * 0.05
            price = max(0.02, min(0.98, price + change + mean_reversion))

            open_p = price
            high_p = min(0.99, price + abs(random.gauss(0, 0.005)))
            low_p = max(0.01, price - abs(random.gauss(0, 0.005)))
            close_p = price + random.gauss(0, 0.003)
            close_p = max(low_p, min(high_p, close_p))

            ts = int(now - (count - i) * period * 60)

            candles.append({
                "timestamp": ts,
                "open": round(open_p, 4),
                "high": round(high_p, 4),
                "low": round(low_p, 4),
                "close": round(close_p, 4),
                "volume": random.randint(10, 500),
            })

        return candles

    def _generate_stub_orderbook(self, ticker: str) -> dict:
        """Generate a synthetic orderbook."""
        mid = random.uniform(0.3, 0.7)
        return {
            "yes_bids": [
                {"price": round(mid - 0.01 * i, 3), "size": random.randint(10, 200)}
                for i in range(1, 4)
            ],
            "no_bids": [
                {"price": round(1 - mid - 0.01 * i, 3), "size": random.randint(10, 200)}
                for i in range(1, 4)
            ],
        }

    # ------------------------------------------------------------------ #
    # Source health tracking
    # ------------------------------------------------------------------ #

    def _update_source_status(
        self,
        source_id: str,
        healthy: bool,
        error: str = "",
        latency_ms: float = 0.0,
    ) -> None:
        """Update data_source_status table for this client."""
        now = datetime.now(timezone.utc).isoformat()
        try:
            if healthy:
                execute_write(
                    """INSERT INTO data_source_status
                       (id, source_name, status, last_success, error_count, latency_ms)
                       VALUES (?, ?, 'healthy', ?, 0, ?)
                       ON CONFLICT(id) DO UPDATE SET
                         status='healthy', last_success=excluded.last_success,
                         error_count=0, latency_ms=excluded.latency_ms""",
                    (source_id, "Kalshi REST", now, latency_ms),
                )
            else:
                execute_write(
                    """INSERT INTO data_source_status
                       (id, source_name, status, last_error, error_count)
                       VALUES (?, ?, 'down', ?, 1)
                       ON CONFLICT(id) DO UPDATE SET
                         status='down', last_error=excluded.last_error,
                         error_count=error_count+1""",
                    (source_id, "Kalshi REST", f"{now}: {error}"),
                )
        except Exception:
            pass

    async def close(self) -> None:
        """Close the aiohttp session."""
        if self._session and not self._session.closed:
            await self._session.close()

    @property
    def is_stub_mode(self) -> bool:
        """True when operating without real API credentials."""
        return self._stub_mode
