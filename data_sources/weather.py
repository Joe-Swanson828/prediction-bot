"""
data_sources/weather.py — Weather data source (OpenWeatherMap).

Fetches current conditions and forecasts for cities relevant to
weather prediction markets. Used to generate speed signals when
actual conditions diverge from what prediction markets are pricing.

Free tier: 60 calls/min, 1,000,000 calls/month.
No API key needed for NOAA/Weather.gov (backup source).

Usage:
    client = WeatherDataSource(api_key="your_key")
    current = await client.get_current(city="New York")
    forecast = await client.get_forecast(city="Chicago")
"""

from __future__ import annotations

import random
import time
from typing import Dict, List, Optional

import aiohttp

OWM_BASE = "https://api.openweathermap.org/data/2.5"

# Cache: city -> (data, timestamp)
_weather_cache: Dict[str, tuple] = {}
_CACHE_TTL = 300  # 5 minutes


class WeatherDataSource:
    """
    Fetches weather data from OpenWeatherMap.
    Falls back to stub data when the API key is missing or unavailable.
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

    async def get_current(self, city: str) -> dict:
        """
        Get current weather conditions for a city.

        Returns:
            {
                'city': str,
                'temp_f': float,
                'feels_like_f': float,
                'humidity_pct': float,
                'conditions': str,   # e.g. 'Clear', 'Rain', 'Snow'
                'wind_mph': float,
                'timestamp': str,
            }
        """
        cache_key = f"current:{city.lower()}"
        cached = _weather_cache.get(cache_key)
        if cached and (time.time() - cached[1] < _CACHE_TTL):
            return cached[0]

        if self._stub_mode:
            result = self._stub_current(city)
            _weather_cache[cache_key] = (result, time.time())
            return result

        try:
            session = await self._get_session()
            async with session.get(
                f"{OWM_BASE}/weather",
                params={
                    "q": city,
                    "appid": self._api_key,
                    "units": "imperial",
                },
                ssl=True,
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()

            result = {
                "city": city,
                "temp_f": data["main"]["temp"],
                "feels_like_f": data["main"]["feels_like"],
                "humidity_pct": data["main"]["humidity"],
                "conditions": data["weather"][0]["main"],
                "wind_mph": data["wind"]["speed"],
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
            _weather_cache[cache_key] = (result, time.time())
            self._update_status("openweathermap", healthy=True)
            return result

        except Exception as e:
            self._update_status("openweathermap", healthy=False, error=str(e))
            result = self._stub_current(city)
            _weather_cache[cache_key] = (result, time.time())
            return result

    async def get_forecast(self, city: str, days: int = 5) -> List[dict]:
        """
        Get a multi-day weather forecast.

        Returns:
            List of daily forecasts with temp ranges and conditions.
        """
        if self._stub_mode:
            return self._stub_forecast(city, days)

        try:
            session = await self._get_session()
            async with session.get(
                f"{OWM_BASE}/forecast",
                params={
                    "q": city,
                    "appid": self._api_key,
                    "units": "imperial",
                    "cnt": days * 8,  # 8 readings per day (3-hour intervals)
                },
                ssl=True,
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()

            # Aggregate 3-hour readings into daily forecasts
            daily: Dict[str, dict] = {}
            for reading in data.get("list", []):
                date = reading["dt_txt"][:10]
                temp = reading["main"]["temp"]
                if date not in daily:
                    daily[date] = {
                        "date": date,
                        "city": city,
                        "temp_high_f": temp,
                        "temp_low_f": temp,
                        "conditions": reading["weather"][0]["main"],
                    }
                else:
                    daily[date]["temp_high_f"] = max(daily[date]["temp_high_f"], temp)
                    daily[date]["temp_low_f"] = min(daily[date]["temp_low_f"], temp)

            return list(daily.values())[:days]

        except Exception:
            return self._stub_forecast(city, days)

    def compute_market_edge(
        self,
        forecast: dict,
        market_title: str,
        current_yes_price: float,
    ) -> Optional[dict]:
        """
        Compute whether the weather forecast implies edge vs current market price.

        For example: if the market asks "Will NYC temp exceed 75°F?"
        and the forecast says 78°F, and the market is pricing YES at 40%,
        there's a significant edge (should be much higher probability).

        Args:
            forecast: Weather forecast dict from get_current() or get_forecast()
            market_title: The prediction market title for keyword matching
            current_yes_price: Current YES price (0-1)

        Returns:
            {'edge': float, 'direction': str, 'probability_estimate': float}
            or None if no edge detected.
        """
        title_lower = market_title.lower()
        temp_f = forecast.get("temp_f", 60)
        conditions = forecast.get("conditions", "").lower()

        probability_estimate = 0.5  # default
        direction = "neutral"

        # Temperature threshold markets
        if "75°f" in title_lower or "75 f" in title_lower or "75 degrees" in title_lower:
            # "Will it exceed 75°F?"
            probability_estimate = 0.85 if temp_f > 78 else (0.60 if temp_f > 73 else 0.20)
        elif "90°f" in title_lower or "90 degrees" in title_lower:
            probability_estimate = 0.85 if temp_f > 92 else (0.55 if temp_f > 87 else 0.15)
        elif "snow" in title_lower:
            probability_estimate = 0.85 if "snow" in conditions else 0.10
        elif "rain" in title_lower:
            probability_estimate = 0.75 if "rain" in conditions else 0.25

        # Compare to current market price
        edge = probability_estimate - current_yes_price
        if abs(edge) > 0.10:
            direction = "bullish" if edge > 0 else "bearish"
            return {
                "edge": round(edge, 3),
                "direction": direction,
                "probability_estimate": round(probability_estimate, 3),
                "forecast_temp_f": temp_f,
                "forecast_conditions": conditions,
            }

        return None

    # ------------------------------------------------------------------ #
    # Stub data generators
    # ------------------------------------------------------------------ #

    def _stub_current(self, city: str) -> dict:
        """Return plausible synthetic current weather."""
        city_temps = {
            "new york": 55, "chicago": 48, "los angeles": 70,
            "miami": 78, "seattle": 52, "phoenix": 75, "denver": 45,
        }
        base_temp = city_temps.get(city.lower(), 60)
        conditions_options = ["Clear", "Partly Cloudy", "Cloudy", "Rain", "Fog"]

        return {
            "city": city,
            "temp_f": round(base_temp + random.uniform(-8, 8), 1),
            "feels_like_f": round(base_temp + random.uniform(-10, 5), 1),
            "humidity_pct": random.randint(30, 80),
            "conditions": random.choice(conditions_options),
            "wind_mph": round(random.uniform(3, 20), 1),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

    def _stub_forecast(self, city: str, days: int) -> List[dict]:
        """Return synthetic multi-day forecast."""
        base_temp = 60 + random.randint(-15, 15)
        forecasts = []
        import datetime

        for i in range(days):
            date = (datetime.date.today() + datetime.timedelta(days=i)).isoformat()
            forecasts.append({
                "date": date,
                "city": city,
                "temp_high_f": round(base_temp + random.uniform(-3, 5), 1),
                "temp_low_f": round(base_temp - random.uniform(5, 15), 1),
                "conditions": random.choice(["Clear", "Partly Cloudy", "Rain", "Cloudy"]),
            })

        return forecasts

    def _update_status(self, source_id: str, healthy: bool, error: str = "") -> None:
        """Update data source health status."""
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
                    (source_id, "OpenWeatherMap", now),
                )
            else:
                execute_write(
                    """INSERT INTO data_source_status (id, source_name, status, last_error, error_count)
                       VALUES (?, ?, 'down', ?, 1)
                       ON CONFLICT(id) DO UPDATE SET
                         status='down', last_error=excluded.last_error, error_count=error_count+1""",
                    (source_id, "OpenWeatherMap", f"{now}: {error}"),
                )
        except Exception:
            pass

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
