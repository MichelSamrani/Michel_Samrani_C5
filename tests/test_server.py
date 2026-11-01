"""Tests for the three MCP tools, run through a real FastMCP client (in-memory).

The Open-Meteo API is replaced by an httpx.MockTransport, so tests are offline
and deterministic. Each tool has a happy-path test and at least one failure case.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

import httpx
import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

import weather_trip_server as srv

TODAY = date.today()
DATES = [(TODAY + timedelta(days=i)).isoformat() for i in range(7)]


def _forecast_payload(rain: list[int], tmax: list[float] | None = None) -> dict:
    n = len(rain)
    tmax = tmax or [22.0] * n
    return {
        "daily": {
            "time": DATES[:n],
            "temperature_2m_max": tmax,
            "temperature_2m_min": [t - 8 for t in tmax],
            "precipitation_probability_max": rain,
            "wind_speed_10m_max": [10.0] * n,
        }
    }


def _mock_api(monkeypatch, *, found=True, rain=(10, 80, 5), tmax=None, fail=False):
    def handler(request: httpx.Request) -> httpx.Response:
        if fail:
            raise httpx.ConnectError("network down", request=request)
        if "geocoding" in request.url.host:
            results = [{"name": "Beirut", "country": "Lebanon", "latitude": 33.89, "longitude": 35.5}]
            return httpx.Response(200, json={"results": results} if found else {})
        days = int(request.url.params["forecast_days"])
        return httpx.Response(200, json=_forecast_payload(list(rain)[:days], tmax and list(tmax)[:days]))

    monkeypatch.setattr(srv, "_http_client", lambda: httpx.Client(transport=httpx.MockTransport(handler)))


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path, monkeypatch):
    monkeypatch.setenv("TRIPS_FILE", str(tmp_path / "trips.json"))
    return tmp_path / "trips.json"


@pytest.fixture
async def client():
    async with Client(srv.mcp) as c:
        yield c


# ------------------------------------------------------------------ discovery
async def test_exactly_three_tools_are_exposed(client):
    names = {t.name for t in await client.list_tools()}
    assert names == {"get_weather_forecast", "save_trip_plan", "plan_outdoor_trip"}


# ------------------------------------------------------------------ data tool
async def test_forecast_returns_typed_days(client, monkeypatch, isolated_storage):
    _mock_api(monkeypatch)
    res = await client.call_tool("get_weather_forecast", {"city": "Beirut", "days": 3})
    data = res.structured_content
    assert data["city"] == "Beirut" and data["country"] == "Lebanon"
    assert [d["precipitation_probability_pct"] for d in data["days"]] == [10, 80, 5]
    assert not isolated_storage.exists(), "data tool must not write anything"


async def test_forecast_unknown_city_fails_clearly(client, monkeypatch):
    _mock_api(monkeypatch, found=False)
    with pytest.raises(ToolError, match="Could not find a city named 'Atlantisxyz'"):
        await client.call_tool("get_weather_forecast", {"city": "Atlantisxyz"})


async def test_forecast_rejects_out_of_range_days(client, monkeypatch):
    _mock_api(monkeypatch)
    with pytest.raises(ToolError, match="days must be between 1 and 7"):
        await client.call_tool("get_weather_forecast", {"city": "Beirut", "days": 30})


async def test_forecast_service_unavailable(client, monkeypatch):
    _mock_api(monkeypatch, fail=True)
    with pytest.raises(ToolError, match="weather service is unavailable"):
        await client.call_tool("get_weather_forecast", {"city": "Beirut"})


# ------------------------------------------------------------------ action tool
async def test_save_trip_persists(client, isolated_storage):
    res = await client.call_tool(
        "save_trip_plan", {"city": "Byblos", "date": DATES[1], "activity": "hiking"}
    )
    assert res.structured_content["id"] == 1
    stored = json.loads(isolated_storage.read_text())
    assert stored[0]["city"] == "Byblos" and stored[0]["date"] == DATES[1]


async def test_save_trip_rejects_bad_date(client, isolated_storage):
    with pytest.raises(ToolError, match="Invalid date '31/12/2026'"):
        await client.call_tool("save_trip_plan", {"city": "Byblos", "date": "31/12/2026", "activity": "hiking"})
    assert not isolated_storage.exists()


async def test_save_trip_rejects_past_date(client):
    yesterday = (TODAY - timedelta(days=1)).isoformat()
    with pytest.raises(ToolError, match="in the past"):
        await client.call_tool("save_trip_plan", {"city": "Byblos", "date": yesterday, "activity": "hiking"})


async def test_save_trip_rejects_duplicate(client):
    args = {"city": "Byblos", "date": DATES[2], "activity": "hiking"}
    await client.call_tool("save_trip_plan", args)
    with pytest.raises(ToolError, match="already saved"):
        await client.call_tool("save_trip_plan", args)


# ------------------------------------------------------------------ orchestration tool
async def test_plan_picks_best_day_and_saves(client, monkeypatch, isolated_storage):
    _mock_api(monkeypatch, rain=(10, 80, 5))
    res = await client.call_tool("plan_outdoor_trip", {"city": "Beirut", "activity": "hiking", "days": 3})
    data = res.structured_content
    assert data["saved"] is True
    assert data["chosen_day"]["date"] == DATES[2]  # lowest rain at equal temperature
    assert json.loads(isolated_storage.read_text())[0]["activity"] == "hiking"


async def test_plan_saves_nothing_when_all_days_rainy(client, monkeypatch, isolated_storage):
    _mock_api(monkeypatch, rain=(90, 85, 70))
    res = await client.call_tool("plan_outdoor_trip", {"city": "Beirut", "activity": "picnic", "days": 3})
    assert res.structured_content["saved"] is False
    assert "no trip was saved" in res.structured_content["reason"]
    assert not isolated_storage.exists()


async def test_plan_propagates_data_tool_error(client, monkeypatch, isolated_storage):
    _mock_api(monkeypatch, found=False)
    with pytest.raises(ToolError, match="Could not find a city"):
        await client.call_tool("plan_outdoor_trip", {"city": "Nowhereville", "activity": "hiking"})
    assert not isolated_storage.exists()


# ------------------------------------------------------------------ resource
async def test_saved_trips_resource(client):
    await client.call_tool("save_trip_plan", {"city": "Tyre", "date": DATES[3], "activity": "beach day"})
    contents = await client.read_resource("trips://saved")
    assert json.loads(contents[0].text)[0]["city"] == "Tyre"
