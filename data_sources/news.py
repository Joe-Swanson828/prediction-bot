"""
data_sources/news.py — News headline source (NewsAPI).

Fetches recent news headlines for sentiment analysis.
Free tier: 100 requests/day.

Caches results in both memory and the sentiment_cache DB table
to minimize redundant API calls.

Usage:
    source = NewsDataSource(api_key="your_key")
    headlines = await source.get_headlines("Bitcoin", from_hours=24)
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import aiohttp

NEWSAPI_BASE = "https://newsapi.org/v2"

# In-memory cache: query -> (headlines, timestamp)
_cache: Dict[str, tuple] = {}
_CACHE_TTL = 1800  # 30 minutes (news doesn't change that fast)

# Default queries per market category
CATEGORY_QUERIES: Dict[str, List[str]] = {
    "crypto": [
        "Bitcoin", "Ethereum", "cryptocurrency", "crypto market",
        "BTC price", "ETH price",
    ],
    "sports": [
        "NFL injury report", "NBA scores", "MLB standings",
        "Super Bowl", "basketball", "football",
    ],
    "weather": [
        "weather forecast", "storm warning", "temperature record",
        "NOAA forecast", "heat wave",
    ],
}


class NewsDataSource:
    """
    Fetches news headlines from NewsAPI for sentiment analysis.
    Returns stub headlines when API key is not configured.
    """

    def __init__(self, api_key: str = "") -> None:
        self._api_key = api_key
        self._stub_mode = not bool(api_key)
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=10)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def get_headlines(
        self,
        query: str,
        from_hours: int = 24,
        language: str = "en",
        max_results: int = 20,
    ) -> List[str]:
        """
        Fetch news headlines for a query string.

        Args:
            query: Search query (e.g., "Bitcoin", "Kansas City Chiefs")
            from_hours: How far back to look (hours)
            language: Language filter
            max_results: Maximum headlines to return

        Returns:
            List of headline strings (title + description).
        """
        # Check memory cache first
        cache_key = f"{query}:{from_hours}"
        cached = _cache.get(cache_key)
        if cached and (time.time() - cached[1] < _CACHE_TTL):
            return cached[0]

        if self._stub_mode:
            result = self._stub_headlines(query)
            _cache[cache_key] = (result, time.time())
            return result

        try:
            from_dt = (datetime.now(timezone.utc) - timedelta(hours=from_hours)).isoformat()

            session = await self._get_session()
            async with session.get(
                f"{NEWSAPI_BASE}/everything",
                params={
                    "q": query,
                    "from": from_dt,
                    "language": language,
                    "sortBy": "publishedAt",
                    "pageSize": min(max_results, 100),
                    "apiKey": self._api_key,
                },
                ssl=True,
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()

            articles = data.get("articles", [])
            headlines = []
            for article in articles:
                title = article.get("title") or ""
                description = article.get("description") or ""
                if title:
                    text = title
                    if description:
                        text += " " + description
                    headlines.append(text)

            # Cache result
            _cache[cache_key] = (headlines, time.time())

            # Also cache in DB for persistence across restarts
            self._cache_to_db(query, headlines)

            self._update_status("newsapi", healthy=True)
            return headlines[:max_results]

        except Exception as e:
            self._update_status("newsapi", healthy=False, error=str(e))
            # Try DB cache as fallback
            db_cached = self._get_db_cache(query)
            if db_cached:
                return db_cached
            result = self._stub_headlines(query)
            _cache[cache_key] = (result, time.time())
            return result

    async def get_category_headlines(
        self, category: str, limit: int = 15
    ) -> List[str]:
        """
        Get headlines for a market category using pre-defined queries.

        Args:
            category: 'sports' | 'crypto' | 'weather'
            limit: Max headlines per query (total will be queries × limit)

        Returns:
            Combined list of headlines for the category.
        """
        queries = CATEGORY_QUERIES.get(category.lower(), [category])
        all_headlines = []

        for query in queries[:3]:  # limit to 3 queries per category to conserve API calls
            headlines = await self.get_headlines(query, from_hours=12, max_results=5)
            all_headlines.extend(headlines)

        # Deduplicate while preserving order
        seen = set()
        unique = []
        for h in all_headlines:
            if h not in seen:
                seen.add(h)
                unique.append(h)

        return unique[:limit]

    # ------------------------------------------------------------------ #
    # Stub data
    # ------------------------------------------------------------------ #

    def _stub_headlines(self, query: str) -> List[str]:
        """Return synthetic news headlines matching the query topic."""
        query_lower = query.lower()

        if any(w in query_lower for w in ["bitcoin", "btc", "crypto", "ethereum", "eth"]):
            return [
                "Bitcoin surges past $95,000 as institutional demand grows",
                "Ethereum network upgrades complete, gas fees at historic lows",
                "Crypto market gains amid positive regulatory signals from SEC",
                "Bitcoin ETF inflows reach record $500M in single day",
                "Analysts predict BTC could reach $120K by end of Q1",
            ]
        elif any(w in query_lower for w in ["nfl", "football", "super bowl", "chiefs"]):
            return [
                "Chiefs continue dominant run toward playoff berth",
                "Key player questionable for upcoming matchup with ankle injury",
                "NFL weekly injury report shows several starters limited in practice",
                "Mahomes posts 350 yards and 3 TDs in dominant performance",
                "Betting lines shift after key injury news ahead of Sunday game",
            ]
        elif any(w in query_lower for w in ["nba", "basketball", "lakers", "celtics"]):
            return [
                "Celtics extend winning streak to 8 games behind stellar defense",
                "Lakers star listed as questionable with back tightness",
                "NBA trade deadline approaches as teams consider moves",
                "Player returns to practice, expected to play in next game",
                "Celtics favored heavily in tonight's matchup per oddsmakers",
            ]
        elif any(w in query_lower for w in ["weather", "temperature", "storm", "rain"]):
            return [
                "Winter storm expected to bring 6-12 inches to Northeast this weekend",
                "Temperature records may fall as unusual warm air mass approaches",
                "NOAA upgrades storm warning for coastal areas",
                "Severe thunderstorm watch issued for multiple states",
                "Forecast models disagree on storm track, uncertainty remains high",
            ]
        else:
            return [
                f"Latest developments in {query} market",
                f"Analysts weigh in on {query} outlook",
                f"Breaking: Major update regarding {query}",
            ]

    # ------------------------------------------------------------------ #
    # DB cache
    # ------------------------------------------------------------------ #

    def _cache_to_db(self, query: str, headlines: List[str]) -> None:
        """Save headlines to DB sentiment_cache for persistence."""
        if not headlines:
            return
        now = datetime.now(timezone.utc).isoformat()
        try:
            from database.connection import execute_write
            combined = " | ".join(headlines[:10])
            execute_write(
                """INSERT INTO sentiment_cache (source, query, sentiment_score, raw_text, timestamp)
                   VALUES ('newsapi', ?, 50, ?, ?)""",
                (query, combined, now),
            )
        except Exception:
            pass

    def _get_db_cache(self, query: str) -> Optional[List[str]]:
        """Try to retrieve cached headlines from DB."""
        try:
            from database.connection import execute_query
            rows = execute_query(
                """SELECT raw_text FROM sentiment_cache
                   WHERE source='newsapi' AND query=?
                   AND timestamp > datetime('now', '-2 hours')
                   ORDER BY timestamp DESC LIMIT 1""",
                (query,),
            )
            if rows and rows[0]["raw_text"]:
                return rows[0]["raw_text"].split(" | ")
        except Exception:
            pass
        return None

    def _update_status(self, source_id: str, healthy: bool, error: str = "") -> None:
        now = datetime.now(timezone.utc).isoformat()
        try:
            from database.connection import execute_write
            if healthy:
                execute_write(
                    """INSERT INTO data_source_status (id, source_name, status, last_success, error_count)
                       VALUES (?, ?, 'healthy', ?, 0)
                       ON CONFLICT(id) DO UPDATE SET
                         status='healthy', last_success=excluded.last_success, error_count=0""",
                    (source_id, "NewsAPI", now),
                )
            else:
                execute_write(
                    """INSERT INTO data_source_status (id, source_name, status, last_error, error_count)
                       VALUES (?, ?, 'down', ?, 1)
                       ON CONFLICT(id) DO UPDATE SET
                         status='down', last_error=excluded.last_error, error_count=error_count+1""",
                    (source_id, "NewsAPI", f"{now}: {error}"),
                )
        except Exception:
            pass

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
