"""
data_sources/crypto.py — Crypto price data source (Binance public API).

Fetches BTC and ETH price data from Binance's public REST API.
No API key required for market data endpoints.

Used to:
  1. Generate speed signals when crypto prices move before prediction markets adjust
  2. Provide underlying asset context for crypto prediction market TA

Rate limits: Binance public API has generous limits (1200 requests/min).
We cache results and only re-fetch when the cache is stale.

Usage:
    client = CryptoDataSource()
    candles = await client.get_candles("BTC", interval="1m", limit=100)
    price = await client.get_current_price("BTC")
"""

from __future__ import annotations

import random
import time
from typing import Dict, List, Optional

import aiohttp

from database.connection import execute_write

# Binance public API — no authentication needed for market data
BINANCE_BASE = "https://api.binance.com"
BINANCE_KLINES = f"{BINANCE_BASE}/api/v3/klines"
BINANCE_TICKER = f"{BINANCE_BASE}/api/v3/ticker/price"

# Symbol mapping: our category names → Binance symbols
SYMBOL_MAP: Dict[str, str] = {
    "BTC": "BTCUSDT",
    "ETH": "ETHUSDT",
    "SOL": "SOLUSDT",
    "BNB": "BNBUSDT",
}

# Interval mapping: our candle period names → Binance interval strings
INTERVAL_MAP: Dict[str, str] = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "1h": "1h",
    "4h": "4h",
    "1d": "1d",
}

# Simple in-memory price cache to respect rate limits
_price_cache: Dict[str, tuple] = {}  # symbol -> (price, timestamp)
_CACHE_TTL_SECONDS = 30


class CryptoDataSource:
    """
    Fetches BTC/ETH/SOL price data from Binance public API.
    Falls back to stub data when the API is unavailable.
    """

    def __init__(self) -> None:
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=10)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def get_candles(
        self,
        symbol: str,        # e.g. 'BTC'
        interval: str = "1m",
        limit: int = 100,
    ) -> List[dict]:
        """
        Fetch OHLCV candlestick data for a crypto asset.

        Args:
            symbol: Crypto symbol ('BTC', 'ETH', 'SOL', etc.)
            interval: Candle period ('1m', '5m', '15m', '1h', '4h', '1d')
            limit: Number of candles (max 1000)

        Returns:
            List of candle dicts: {timestamp, open, high, low, close, volume}
        """
        binance_symbol = SYMBOL_MAP.get(symbol.upper(), symbol.upper() + "USDT")
        binance_interval = INTERVAL_MAP.get(interval, "1m")

        try:
            session = await self._get_session()
            async with session.get(
                BINANCE_KLINES,
                params={
                    "symbol": binance_symbol,
                    "interval": binance_interval,
                    "limit": min(limit, 1000),
                },
                ssl=True,
            ) as resp:
                resp.raise_for_status()
                raw = await resp.json()

            self._update_status("binance", healthy=True)

            # Binance klines format: [open_time, open, high, low, close, volume, ...]
            # All values are strings — must cast to float
            return [
                {
                    "timestamp": int(c[0]) // 1000,  # ms → seconds
                    "open": float(c[1]),
                    "high": float(c[2]),
                    "low": float(c[3]),
                    "close": float(c[4]),
                    "volume": float(c[5]),
                }
                for c in raw
            ]

        except Exception as e:
            self._update_status("binance", healthy=False, error=str(e))
            return self._generate_stub_candles(symbol, limit)

    async def get_current_price(self, symbol: str) -> Optional[float]:
        """
        Get the current spot price for a crypto asset.
        Uses a 30-second cache to avoid excessive API calls.
        """
        key = symbol.upper()
        cached = _price_cache.get(key)
        if cached:
            price, ts = cached
            if time.time() - ts < _CACHE_TTL_SECONDS:
                return price

        binance_symbol = SYMBOL_MAP.get(key, key + "USDT")

        try:
            session = await self._get_session()
            async with session.get(
                BINANCE_TICKER,
                params={"symbol": binance_symbol},
                ssl=True,
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()

            price = float(data["price"])
            _price_cache[key] = (price, time.time())
            return price

        except Exception:
            # Return last cached value or stub
            if cached:
                return cached[0]
            return self._stub_price(symbol)

    async def get_all_prices(self) -> Dict[str, float]:
        """Fetch current prices for all tracked crypto assets."""
        prices = {}
        for symbol in SYMBOL_MAP:
            price = await self.get_current_price(symbol)
            if price:
                prices[symbol] = price
        return prices

    # ------------------------------------------------------------------ #
    # Stub data generators
    # ------------------------------------------------------------------ #

    def _generate_stub_candles(self, symbol: str, count: int) -> List[dict]:
        """Generate synthetic price candles for testing without API access."""
        base_prices = {"BTC": 95000, "ETH": 3200, "SOL": 180, "BNB": 450}
        base = base_prices.get(symbol.upper(), 100)

        candles = []
        price = base
        now = int(time.time())

        for i in range(count):
            change_pct = random.gauss(0, 0.008)
            price = max(base * 0.5, min(base * 2, price * (1 + change_pct)))

            candles.append({
                "timestamp": now - (count - i) * 60,
                "open": round(price * random.uniform(0.998, 1.002), 2),
                "high": round(price * random.uniform(1.001, 1.010), 2),
                "low": round(price * random.uniform(0.990, 0.999), 2),
                "close": round(price, 2),
                "volume": round(random.uniform(10, 1000), 2),
            })

        return candles

    def _stub_price(self, symbol: str) -> float:
        """Return a plausible stub price for a crypto asset."""
        base_prices = {"BTC": 95000, "ETH": 3200, "SOL": 180, "BNB": 450}
        base = base_prices.get(symbol.upper(), 100)
        return round(base * random.uniform(0.95, 1.05), 2)

    def _update_status(
        self, source_id: str, healthy: bool, error: str = "", latency_ms: float = 0.0
    ) -> None:
        """Update data source health status in the DB."""
        from datetime import timezone
        now = __import__("datetime").datetime.now(timezone.utc).isoformat()
        try:
            if healthy:
                execute_write(
                    """INSERT INTO data_source_status
                       (id, source_name, status, last_success, error_count, latency_ms)
                       VALUES (?, ?, 'healthy', ?, 0, ?)
                       ON CONFLICT(id) DO UPDATE SET
                         status='healthy', last_success=excluded.last_success,
                         error_count=0, latency_ms=excluded.latency_ms""",
                    (source_id, "Binance", now, latency_ms),
                )
            else:
                execute_write(
                    """INSERT INTO data_source_status
                       (id, source_name, status, last_error, error_count)
                       VALUES (?, ?, 'down', ?, 1)
                       ON CONFLICT(id) DO UPDATE SET
                         status='down', last_error=excluded.last_error,
                         error_count=error_count+1""",
                    (source_id, "Binance", f"{now}: {error}"),
                )
        except Exception:
            pass

    async def close(self) -> None:
        """Close the HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()
