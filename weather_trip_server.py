"""Weather & Trip Planner - a FastMCP server exposing exactly three tools.

    Data tool          -> get_weather_forecast   (read-only lookup, Open-Meteo API)
    Action tool        -> save_trip_plan         (writes a plan to a local JSON file)
    Orchestration tool -> plan_outdoor_trip      (forecast -> pick best day -> save)

Plus one read-only resource:  trips://saved  (list of saved plans).

Run directly over stdio:   python weather_trip_server.py
"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path

import httpx
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import BaseModel, Field

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
HTTP_TIMEOUT_S = 10.0
MAX_FORECAST_DAYS = 7

# Days with a rain probability above this are never recommended for outdoor plans.
MAX_ACCEPTABLE_RAIN_PCT = 60

mcp = FastMCP(
    name="weather-trip-planner",
    instructions=(
        "Tools for planning outdoor day trips. Use get_weather_forecast to look up "
        "weather, save_trip_plan to store a plan the user has chosen, and "
        "plan_outdoor_trip when the user wants you to pick the best day and save it."
    ),
)


# --------------------------------------------------------------------------- #
# Typed outputs
# --------------------------------------------------------------------------- #
class DailyForecast(BaseModel):
    date: str = Field(description="ISO date, YYYY-MM-DD")
    temp_max_c: float
    temp_min_c: float
    precipitation_probability_pct: int = Field(description="Max chance of rain that day, 0-100")
    wind_speed_max_kmh: float


class Forecast(BaseModel):
    city: str = Field(description="Resolved city name")
    country: str
    latitude: float
    longitude: float
    days: list[DailyForecast]


class SavedTrip(BaseModel):
    id: int
    city: str
    date: str
    activity: str
    notes: str


class TripPlanResult(BaseModel):
    saved: bool = Field(description="True if a trip plan was stored")
    reason: str = Field(description="Why this day was chosen, or why nothing was saved")
    chosen_day: DailyForecast | None = None
    trip: SavedTrip | None = None
    forecast: Forecast


# --------------------------------------------------------------------------- #
# Storage helpers (path is overridable so tests never touch the real file)
# --------------------------------------------------------------------------- #
def _trips_file() -> Path:
    return Path(os.environ.get("TRIPS_FILE", Path(__file__).parent / "data" / "trips.json"))


def _load_trips() -> list[dict]:
    path = _trips_file()
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ToolError(f"Trip storage at {path} is corrupted and could not be read.") from exc


def _write_trips(trips: list[dict]) -> None:
    path = _trips_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(trips, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------- #
# HTTP helper (module-level so tests can swap in an httpx.MockTransport)
# --------------------------------------------------------------------------- #
def _http_client() -> httpx.Client:
    return httpx.Client(timeout=HTTP_TIMEOUT_S)


def _get_json(url: str, params: dict) -> dict:
    try:
        with _http_client() as client:
            resp = client.get(url, params=params)
            resp.raise_for_status()
            return resp.json()
    except httpx.TimeoutException as exc:
        raise ToolError("The weather service timed out. Please try again in a moment.") from exc
    except httpx.HTTPError as exc:
        raise ToolError(f"The weather service is unavailable right now ({exc.__class__.__name__}).") from exc


# --------------------------------------------------------------------------- #
# Core logic (plain functions, so the orchestration tool can reuse them)
# --------------------------------------------------------------------------- #
def fetch_forecast(city: str, days: int = 3) -> Forecast:
    city = city.strip()
    if not city:
        raise ToolError("City name must not be empty.")
    if not 1 <= days <= MAX_FORECAST_DAYS:
        raise ToolError(f"days must be between 1 and {MAX_FORECAST_DAYS}, got {days}.")

    geo = _get_json(GEOCODE_URL, {"name": city, "count": 1, "language": "en", "format": "json"})
    results = geo.get("results") or []
    if not results:
        raise ToolError(f"Could not find a city named '{city}'. Check the spelling or add the country.")
    place = results[0]

    wx = _get_json(
        FORECAST_URL,
        {
            "latitude": place["latitude"],
            "longitude": place["longitude"],
            "daily": "temperature_2m_max,temperature_2m_min,"
            "precipitation_probability_max,wind_speed_10m_max",
            "forecast_days": days,
            "timezone": "auto",
        },
    )
    daily = wx.get("daily")
    if not daily or not daily.get("time"):
        raise ToolError("The weather service returned no daily forecast data.")

    day_list = [
        DailyForecast(
            date=daily["time"][i],
            temp_max_c=daily["temperature_2m_max"][i],
            temp_min_c=daily["temperature_2m_min"][i],
            precipitation_probability_pct=daily["precipitation_probability_max"][i] or 0,
            wind_speed_max_kmh=daily["wind_speed_10m_max"][i],
        )
        for i in range(len(daily["time"]))
    ]
    return Forecast(
        city=place["name"],
        country=place.get("country", ""),
        latitude=place["latitude"],
        longitude=place["longitude"],
        days=day_list,
    )


def store_trip(city: str, trip_date: str, activity: str, notes: str = "") -> SavedTrip:
    city, activity = city.strip(), activity.strip()
    if not city:
        raise ToolError("City name must not be empty.")
    if not activity:
        raise ToolError("Activity must not be empty.")
    try:
        parsed = date.fromisoformat(trip_date)
    except ValueError as exc:
        raise ToolError(f"Invalid date '{trip_date}'. Use the format YYYY-MM-DD.") from exc
    if parsed < date.today():
        raise ToolError(f"Cannot save a trip in the past ({trip_date}).")

    trips = _load_trips()
    for t in trips:
        if t["city"].lower() == city.lower() and t["date"] == trip_date and t["activity"].lower() == activity.lower():
            raise ToolError(f"A '{activity}' trip to {city} on {trip_date} is already saved (id {t['id']}).")

    trip = SavedTrip(
        id=max((t["id"] for t in trips), default=0) + 1,
        city=city,
        date=trip_date,
        activity=activity,
        notes=notes.strip(),
    )
    trips.append(trip.model_dump())
    _write_trips(trips)
    return trip


def _comfort_score(day: DailyForecast) -> float:
    """Lower is better: rain chance dominates, then distance from ~22 C, then wind."""
    return day.precipitation_probability_pct + 2 * abs(day.temp_max_c - 22) + 0.5 * day.wind_speed_max_kmh


# --------------------------------------------------------------------------- #
# MCP tools
# --------------------------------------------------------------------------- #
@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
def get_weather_forecast(city: str, days: int = 3) -> Forecast:
    """[DATA] Look up the daily weather forecast for a city. Changes nothing.

    Args:
        city: City name, optionally with country (e.g. "Beirut" or "Paris, France").
        days: Number of days to forecast, starting today (1-7). Defaults to 3.

    Returns:
        The resolved city plus one entry per day with max/min temperature (C),
        max precipitation probability (%) and max wind speed (km/h).

    Errors:
        Unknown city, days out of range, or the weather service being unreachable.
    """
    return fetch_forecast(city, days)


@mcp.tool(annotations={"readOnlyHint": False, "destructiveHint": False, "idempotentHint": False})
def save_trip_plan(city: str, date: str, activity: str, notes: str = "") -> SavedTrip:
    """[ACTION] Save a trip plan to the user's saved trips list.

    Args:
        city: Destination city.
        date: Trip date in YYYY-MM-DD format; must be today or later.
        activity: What the user plans to do (e.g. "hiking", "beach day").
        notes: Optional free-text notes (e.g. a weather summary).

    Returns:
        The stored trip including its new numeric id.

    Errors:
        Malformed or past date, empty city/activity, or an identical trip already saved.
    """
    return store_trip(city, date, activity, notes)


@mcp.tool(annotations={"readOnlyHint": False, "openWorldHint": True})
def plan_outdoor_trip(city: str, activity: str, days: int = 5) -> TripPlanResult:
    """[ORCHESTRATION] Pick the best-weather day for an outdoor activity and save it.

    Calls the forecast lookup for the next `days` days, ranks days by rain chance,
    temperature comfort (~22 C ideal) and wind, then saves the best one as a trip
    plan. If every day has more than 60% chance of rain, nothing is saved and the
    reason is returned instead.

    Args:
        city: Destination city.
        activity: Planned outdoor activity (e.g. "hiking").
        days: How many upcoming days to consider (1-7). Defaults to 5.

    Returns:
        Whether a plan was saved, the reasoning, the chosen day, the saved trip,
        and the full forecast used for the decision.
    """
    forecast = fetch_forecast(city, days)                           # step 1: data tool logic
    candidates = [d for d in forecast.days if d.precipitation_probability_pct <= MAX_ACCEPTABLE_RAIN_PCT]
    if not candidates:
        return TripPlanResult(
            saved=False,
            reason=f"Every day in the next {days} day(s) has over {MAX_ACCEPTABLE_RAIN_PCT}% "
            f"chance of rain in {forecast.city}; no trip was saved.",
            forecast=forecast,
        )

    best = min(candidates, key=_comfort_score)                      # step 2: decide
    summary = (
        f"{best.temp_min_c:.0f}-{best.temp_max_c:.0f} C, "
        f"{best.precipitation_probability_pct}% rain, wind up to {best.wind_speed_max_kmh:.0f} km/h"
    )
    trip = store_trip(forecast.city, best.date, activity, f"Auto-planned: {summary}")  # step 3: action
    return TripPlanResult(
        saved=True,
        reason=f"{best.date} has the best outdoor conditions ({summary}).",
        chosen_day=best,
        trip=trip,
        forecast=forecast,
    )


# --------------------------------------------------------------------------- #
# MCP resource (pure data lookup)
# --------------------------------------------------------------------------- #
@mcp.resource("trips://saved", mime_type="application/json")
def saved_trips() -> str:
    """All trip plans saved so far, as a JSON array."""
    return json.dumps(_load_trips(), indent=2)


if __name__ == "__main__":
    mcp.run(show_banner=False)
