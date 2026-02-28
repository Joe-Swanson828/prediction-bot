"""
exchanges/polymarket.py — Polymarket CLOB API client.

Wraps py-clob-client for Polymarket access with:
  - Stub mode when wallet credentials are not configured
  - Async wrapper around synchronous py-clob-client methods
  - Proper error handling and source status tracking

CRITICAL: web3 must be pinned to version 6.14.0.
  pip install web3==6.14.0
  pip install py-clob-client

py-clob-client uses internal web3 APIs that changed incompatibly in 7.x.
If you see ABI encoding errors, check your web3 version first.

IMPORTANT: US persons are restricted from trading on Polymarket per their ToS.
The bot can read public market data globally without restriction.
Trading requires non-US jurisdiction. Ensure compliance with your local laws.

API docs: https://docs.polymarket.com
"""

from __future__ import annotations

import asyncio
import random
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, List, Optional

from database.connection import execute_write

# Try to import py-clob-client (may not be installed yet)
try:
    from py_clob_client.client import ClobClient
    from py_clob_client.constants import POLYGON
    _CLOB_AVAILABLE = True
except ImportError:
    _CLOB_AVAILABLE = False
    ClobClient = None
    POLYGON = 137

# Stub markets for Polymarket (uses token IDs in production)
_STUB_POLY_MARKETS = [
    ("poly_btc_100k_2025", "Will Bitcoin reach $100K in 2025?", "crypto", 0.58),
    ("poly_eth_10k_2025", "Will Ethereum reach $10K in 2025?", "crypto", 0.22),
    ("poly_lakers_finals", "Will the Lakers make the NBA Finals 2025?", "sports", 0.31),
    ("poly_nyc_75f_june", "NYC average temp above 75°F in June 2025?", "weather", 0.65),
    ("poly_nfl_chiefs", "Will the Chiefs win Super Bowl LX?", "sports", 0.38),
]


class PolymarketClient:
    """
    Polymarket CLOB API client with stub mode support.

    All py-clob-client methods are synchronous. This client wraps them
    in asyncio.get_event_loop().run_in_executor() to avoid blocking the
    main event loop.
    """

    def __init__(self, private_key: str = "", funder_address: str = "") -> None:
        self._private_key = private_key
        self._funder_address = funder_address
        self._client: Optional[Any] = None
        self._stub_mode = True

        if private_key and _CLOB_AVAILABLE:
            self._init_client()

    def _init_client(self) -> None:
        """Initialize the CLOB client with wallet credentials."""
        try:
            self._client = ClobClient(
                host="https://clob.polymarket.com",
                chain_id=POLYGON,
                key=self._private_key,
                funder=self._funder_address or None,
            )
            self._stub_mode = False
        except Exception as e:
            print(f"[PolymarketClient] Failed to initialize CLOB client: {e}")
            self._client = None
            self._stub_mode = True

    async def _run_sync(self, func: Callable, *args, **kwargs) -> Any:
        """
        Run a synchronous py-clob-client method in a thread pool executor
        to avoid blocking the asyncio event loop.
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: func(*args, **kwargs))

    # ------------------------------------------------------------------ #
    # Market data (public, no auth required)
    # ------------------------------------------------------------------ #

    async def get_markets(
        self, category: Optional[str] = None, limit: int = 100
    ) -> List[dict]:
        """
        Fetch active Polymarket prediction markets.

        In stub mode: returns synthetic markets matching the category filter.
        In real mode: uses the CLOB client to fetch live markets.
        """
        if self._stub_mode or not self._client:
            return self._generate_stub_markets(category)

        try:
            start_t = time.time()
            raw = await self._run_sync(self._client.get_markets)
            latency_ms = (time.time() - start_t) * 1000

            # Normalize to our standard format
            markets = []
            for m in (raw or []):
                markets.append(self._normalize_market(m))

            # Filter by category if requested
            if category:
                cat = category.lower()
                markets = [m for m in markets if m.get("category", "").lower() == cat]

            self._update_source_status("polymarket_clob", healthy=True, latency_ms=latency_ms)
            return markets[:limit]

        except Exception as e:
            self._update_source_status("polymarket_clob", healthy=False, error=str(e))
            return self._generate_stub_markets(category)

    async def get_price(
        self, token_id: str, side: str = "BUY"
    ) -> Optional[float]:
        """
        Get the current price for a token.

        Args:
            token_id: The YES or NO token ID
            side: 'BUY' or 'SELL'

        Returns:
            Price as float in [0, 1], or None if unavailable.
        """
        if self._stub_mode or not self._client:
            return round(random.uniform(0.2, 0.8), 4)

        try:
            price_str = await self._run_sync(self._client.get_price, token_id, side)
            return float(price_str) if price_str else None
        except Exception:
            return None

    async def get_midpoint(self, token_id: str) -> Optional[float]:
        """Get the midpoint price for a token."""
        if self._stub_mode or not self._client:
            return round(random.uniform(0.2, 0.8), 4)

        try:
            mid_str = await self._run_sync(self._client.get_midpoint, token_id)
            return float(mid_str) if mid_str else None
        except Exception:
            return None

    async def get_orderbook(self, token_id: str) -> dict:
        """Get the orderbook for a token."""
        if self._stub_mode or not self._client:
            return self._generate_stub_orderbook(token_id)

        try:
            book = await self._run_sync(self._client.get_order_book, token_id)
            return book or {}
        except Exception:
            return self._generate_stub_orderbook(token_id)

    async def get_last_trade_price(self, token_id: str) -> Optional[float]:
        """Get the most recent trade price for a token."""
        if self._stub_mode or not self._client:
            return round(random.uniform(0.2, 0.8), 4)

        try:
            result = await self._run_sync(self._client.get_last_trade_price, token_id)
            if result:
                return float(result.get("price", 0.5))
            return None
        except Exception:
            return None

    # ------------------------------------------------------------------ #
    # Order placement (live mode only)
    # ------------------------------------------------------------------ #

    async def place_order(
        self,
        token_id: str,
        side: str,          # 'BUY' | 'SELL'
        price: float,       # 0.0-1.0
        size: float,        # dollar size
    ) -> Optional[dict]:
        """
        Place a limit order. Only works in live mode with wallet configured.
        Returns None in paper/stub mode.
        """
        from config import config

        if self._stub_mode or config.is_paper_mode:
            return None

        if not self._client:
            return None

        try:
            order = await self._run_sync(
                self._client.create_order,
                {
                    "token_id": token_id,
                    "side": side,
                    "price": str(price),
                    "size": str(size),
                },
            )
            if order:
                response = await self._run_sync(self._client.post_order, order)
                return response
        except Exception as e:
            print(f"[PolymarketClient] Order placement failed: {e}")
        return None

    # ------------------------------------------------------------------ #
    # Stub data generators
    # ------------------------------------------------------------------ #

    def _generate_stub_markets(self, category_filter: Optional[str] = None) -> List[dict]:
        """Return synthetic Polymarket markets for paper trading without credentials."""
        markets = []
        for market_id, title, category, base_price in _STUB_POLY_MARKETS:
            if category_filter and category_filter.lower() != category:
                continue

            noise = random.uniform(-0.03, 0.03)
            yes_price = max(0.02, min(0.98, base_price + noise))

            markets.append({
                "id": market_id,
                "ticker": market_id,
                "title": title,
                "category": category,
                "yes_price": round(yes_price, 4),
                "no_price": round(1.0 - yes_price, 4),
                "volume": random.randint(500, 50000),
                "open_interest": random.randint(100, 10000),
                "status": "active",
                "close_date": (
                    datetime.now(timezone.utc) + timedelta(days=random.randint(5, 60))
                ).isoformat(),
                "exchange": "polymarket",
            })

        return markets

    def _generate_stub_orderbook(self, token_id: str) -> dict:
        """Generate a synthetic orderbook."""
        mid = random.uniform(0.3, 0.7)
        return {
            "bids": [
                {"price": str(round(mid - 0.01 * i, 3)), "size": str(random.randint(10, 500))}
                for i in range(1, 4)
            ],
            "asks": [
                {"price": str(round(mid + 0.01 * i, 3)), "size": str(random.randint(10, 500))}
                for i in range(1, 4)
            ],
        }

    def _normalize_market(self, raw: dict) -> dict:
        """Normalize a raw CLOB API market dict to our standard format."""
        return {
            "id": raw.get("condition_id") or raw.get("question_id") or str(raw),
            "ticker": raw.get("condition_id", ""),
            "title": raw.get("question", ""),
            "category": self._infer_category(raw.get("question", "")),
            "yes_price": float(raw.get("bestBid") or raw.get("best_bid") or 0.5),
            "no_price": 1.0 - float(raw.get("bestBid") or 0.5),
            "volume": float(raw.get("volume") or 0),
            "status": "active" if raw.get("active") else "closed",
            "close_date": raw.get("end_date_iso", ""),
            "exchange": "polymarket",
        }

    def _infer_category(self, title: str) -> str:
        """Infer market category from title text."""
        title_lower = title.lower()
        crypto_keywords = ["bitcoin", "btc", "ethereum", "eth", "crypto", "nft", "defi"]
        weather_keywords = ["temperature", "rain", "snow", "storm", "weather", "hurricane"]
        sports_keywords = [
            "nfl", "nba", "mlb", "nhl", "championship", "super bowl",
            "world series", "finals", "playoffs", "win", "score",
        ]

        for kw in crypto_keywords:
            if kw in title_lower:
                return "crypto"
        for kw in weather_keywords:
            if kw in title_lower:
                return "weather"
        for kw in sports_keywords:
            if kw in title_lower:
                return "sports"

        return "other"

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
                    (source_id, "Polymarket CLOB", now, latency_ms),
                )
            else:
                execute_write(
                    """INSERT INTO data_source_status
                       (id, source_name, status, last_error, error_count)
                       VALUES (?, ?, 'down', ?, 1)
                       ON CONFLICT(id) DO UPDATE SET
                         status='down', last_error=excluded.last_error,
                         error_count=error_count+1""",
                    (source_id, "Polymarket CLOB", f"{now}: {error}"),
                )
        except Exception:
            pass

    @property
    def is_stub_mode(self) -> bool:
        """True when operating without real wallet credentials."""
        return self._stub_mode
