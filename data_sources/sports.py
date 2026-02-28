"""
data_sources/sports.py — Sports data source (The Odds API).

Fetches upcoming game schedules and moneyline odds from multiple
sportsbooks. Used to:
  1. Generate speed signals when sportsbook lines diverge from prediction markets
  2. Provide underlying game context for sports prediction market TA

Free tier: 500 requests/month.
API docs: https://the-odds-api.com/lif-guide.html

Usage:
    client = SportsDataSource(api_key="your_key")
    games = await client.get_upcoming_games(sport="americanfootball_nfl")
"""

from __future__ import annotations

import random
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import aiohttp

ODDS_API_BASE = "https://api.the-odds-api.com/v4"

# Sport slugs supported by The Odds API
SPORT_SLUGS: Dict[str, str] = {
    "nfl": "americanfootball_nfl",
    "nba": "basketball_nba",
    "mlb": "baseball_mlb",
    "nhl": "icehockey_nhl",
    "ncaaf": "americanfootball_ncaaf",
}

# Simple cache
_games_cache: Dict[str, tuple] = {}
_CACHE_TTL = 600  # 10 minutes


class SportsDataSource:
    """
    Fetches sports odds and schedules from The Odds API.
    Falls back to stub data when API key is not configured.
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

    async def get_upcoming_games(
        self, sport: str = "nfl", regions: str = "us", limit: int = 20
    ) -> List[dict]:
        """
        Fetch upcoming games with moneyline odds from multiple sportsbooks.

        Args:
            sport: Sport code ('nfl', 'nba', 'mlb', 'nhl') or full slug
            regions: Regions for odds ('us', 'uk', 'eu', 'au')
            limit: Maximum games to return

        Returns:
            List of game dicts with implied probabilities.
        """
        sport_slug = SPORT_SLUGS.get(sport.lower(), sport)
        cache_key = f"{sport_slug}:{regions}"

        cached = _games_cache.get(cache_key)
        if cached and (time.time() - cached[1] < _CACHE_TTL):
            return cached[0]

        if self._stub_mode:
            result = self._generate_stub_games(sport, limit)
            _games_cache[cache_key] = (result, time.time())
            return result

        try:
            session = await self._get_session()
            async with session.get(
                f"{ODDS_API_BASE}/sports/{sport_slug}/odds",
                params={
                    "apiKey": self._api_key,
                    "regions": regions,
                    "markets": "h2h",  # head-to-head / moneyline
                    "oddsFormat": "american",
                    "dateFormat": "iso",
                },
                ssl=True,
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()

            games = [self._normalize_game(g) for g in (data or [])]
            result = games[:limit]
            _games_cache[cache_key] = (result, time.time())
            self._update_status("the_odds_api", healthy=True)
            return result

        except Exception as e:
            self._update_status("the_odds_api", healthy=False, error=str(e))
            result = self._generate_stub_games(sport, limit)
            _games_cache[cache_key] = (result, time.time())
            return result

    def _normalize_game(self, raw: dict) -> dict:
        """Normalize The Odds API game dict to our standard format."""
        home = raw.get("home_team", "")
        away = raw.get("away_team", "")

        # Extract best odds for each team from bookmakers
        home_prob = 0.5
        away_prob = 0.5

        for bookmaker in raw.get("bookmakers", []):
            for market in bookmaker.get("markets", []):
                if market.get("key") == "h2h":
                    outcomes = market.get("outcomes", [])
                    for outcome in outcomes:
                        price = outcome.get("price", 0)
                        if price != 0:
                            # Convert American odds to probability
                            prob = self._american_to_prob(price)
                            if outcome.get("name") == home:
                                home_prob = prob
                            elif outcome.get("name") == away:
                                away_prob = prob
                    break
            break  # just use first bookmaker for now

        # Normalize probabilities (they won't sum to 1 due to vig)
        total = home_prob + away_prob
        if total > 0:
            home_prob = home_prob / total
            away_prob = away_prob / total

        return {
            "id": raw.get("id", ""),
            "sport": raw.get("sport_key", ""),
            "home_team": home,
            "away_team": away,
            "commence_time": raw.get("commence_time", ""),
            "home_win_prob": round(home_prob, 4),
            "away_win_prob": round(away_prob, 4),
            "bookmaker": raw.get("bookmakers", [{}])[0].get("title", "") if raw.get("bookmakers") else "",
        }

    @staticmethod
    def _american_to_prob(american_odds: int) -> float:
        """Convert American odds (e.g., +150 or -200) to implied probability."""
        if american_odds > 0:
            return 100 / (american_odds + 100)
        else:
            return abs(american_odds) / (abs(american_odds) + 100)

    def compute_prediction_market_edge(
        self,
        game: dict,
        market_title: str,
        current_yes_price: float,
    ) -> Optional[dict]:
        """
        Compare sportsbook odds to prediction market pricing.

        If a sportsbook prices Team A to win at 75% implied probability
        but the prediction market has "Team A wins?" at 60%, there's a 15%
        edge opportunity.
        """
        title_lower = market_title.lower()
        home = game.get("home_team", "").lower()
        away = game.get("away_team", "").lower()

        # Try to match the market to the game
        home_in_title = any(word in title_lower for word in home.split() if len(word) > 3)
        away_in_title = any(word in title_lower for word in away.split() if len(word) > 3)

        if not (home_in_title or away_in_title):
            return None

        # Determine which team the market is betting on
        if home_in_title:
            sportsbook_prob = game.get("home_win_prob", 0.5)
        else:
            sportsbook_prob = game.get("away_win_prob", 0.5)

        edge = sportsbook_prob - current_yes_price

        if abs(edge) > 0.08:  # >8% divergence = potential edge
            return {
                "edge": round(edge, 3),
                "direction": "bullish" if edge > 0 else "bearish",
                "sportsbook_probability": round(sportsbook_prob, 3),
                "market_price": round(current_yes_price, 3),
            }

        return None

    # ------------------------------------------------------------------ #
    # Stub data
    # ------------------------------------------------------------------ #

    def _generate_stub_games(self, sport: str, count: int) -> List[dict]:
        """Generate synthetic upcoming games for testing."""
        nfl_teams = [
            "Kansas City Chiefs", "San Francisco 49ers", "Baltimore Ravens",
            "Philadelphia Eagles", "Dallas Cowboys", "Buffalo Bills",
            "Miami Dolphins", "Detroit Lions", "New York Giants",
        ]
        nba_teams = [
            "Boston Celtics", "Denver Nuggets", "Golden State Warriors",
            "Los Angeles Lakers", "Milwaukee Bucks", "Phoenix Suns",
            "Dallas Mavericks", "Cleveland Cavaliers",
        ]

        teams = nba_teams if "nba" in sport.lower() else nfl_teams
        games = []

        for i in range(min(count, len(teams) // 2)):
            home = teams[i * 2]
            away = teams[i * 2 + 1]
            home_prob = random.uniform(0.35, 0.65)
            commence = (datetime.now(timezone.utc) + timedelta(days=random.randint(1, 14))).isoformat()

            games.append({
                "id": f"stub_{sport}_{i}",
                "sport": sport,
                "home_team": home,
                "away_team": away,
                "commence_time": commence,
                "home_win_prob": round(home_prob, 4),
                "away_win_prob": round(1 - home_prob, 4),
                "bookmaker": "stub_data",
            })

        return games

    def _update_status(self, source_id: str, healthy: bool, error: str = "") -> None:
        import datetime
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        try:
            from database.connection import execute_write
            if healthy:
                execute_write(
                    """INSERT INTO data_source_status (id, source_name, status, last_success, error_count)
                       VALUES (?, ?, 'healthy', ?, 0)
                       ON CONFLICT(id) DO UPDATE SET
                         status='healthy', last_success=excluded.last_success, error_count=0""",
                    (source_id, "The Odds API", now),
                )
            else:
                execute_write(
                    """INSERT INTO data_source_status (id, source_name, status, last_error, error_count)
                       VALUES (?, ?, 'down', ?, 1)
                       ON CONFLICT(id) DO UPDATE SET
                         status='down', last_error=excluded.last_error, error_count=error_count+1""",
                    (source_id, "The Odds API", f"{now}: {error}"),
                )
        except Exception:
            pass

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
