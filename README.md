# Weather & Trip Planner: an MCP toolkit

EECE 503P, Assignment C5. A FastMCP server with exactly **three tools** (one Data, one Action, one Orchestration) and an OpenAI Agents SDK agent that uses them through MCP.

| Category | Tool | What it does | Side effects |
|---|---|---|---|
| **Data** | `get_weather_forecast(city, days=3)` | Geocodes the city and returns a 1–7 day forecast (temp, rain %, wind) from Open-Meteo | None (read-only) |
| **Action** | `save_trip_plan(city, date, activity, notes="")` | Stores a trip plan in `data/trips.json` | Writes a file |
| **Orchestration** | `plan_outdoor_trip(city, activity, days=5)` | Gets the forecast, ranks days by rain, temperature and wind, then saves the best day | Writes a file (only if a suitable day exists) |
| *Resource* | `trips://saved` | JSON list of all saved trips | None |

Weather data comes from [Open-Meteo](https://open-meteo.com/), which is free and needs no API key.

## Architecture

```
 User ──► Agent (OpenAI Agents SDK)  = MCP Host
             │
             ▼
          MCPServerStdio             = MCP Client  (spawns server, JSON-RPC over stdio)
             │
             ▼
          weather_trip_server.py     = MCP Server  (FastMCP)
             ├─ get_weather_forecast ──► Open-Meteo geocoding + forecast APIs
             ├─ save_trip_plan       ──► data/trips.json
             └─ plan_outdoor_trip    ──► forecast → rank days → save
```

## Failure handling

Each failure raises a `ToolError` with a clear message, so the agent can explain it to the user:

| Failure | Message |
|---|---|
| Unknown city | `Could not find a city named 'X'. Check the spelling or add the country.` |
| `days` outside 1–7 | `days must be between 1 and 7, got 30.` |
| API down or timeout | `The weather service is unavailable right now (...)` / `...timed out` |
| Bad date format | `Invalid date 'next friday'. Use the format YYYY-MM-DD.` |
| Date in the past | `Cannot save a trip in the past (...)` |
| Duplicate trip | `A 'hiking' trip to Byblos on ... is already saved (id 1).` |
| All days rainy (orchestration) | Returns normally with `saved: false` and the reason. This is a predictable outcome, not an error. |

## Setup

Requires Python 3.10+.

```bash
python -m venv .venv
# Windows:   .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

Set your OpenAI key (only the agent needs it; the server and tests do not):

```bash
# PowerShell
$env:OPENAI_API_KEY = "sk-..."
# bash
export OPENAI_API_KEY="sk-..."
```

Optionally set `OPENAI_MODEL` to override the Agents SDK default model.

## Running

**Agent (interactive chat):**
```bash
python agent.py
```
**Agent (single query):**
```bash
python agent.py "What's the weather in Beirut for the next 3 days?"
```
The agent prints each MCP round trip, for example:
```
  [agent -> MCP server] call plan_outdoor_trip({"city":"Beirut","activity":"hiking","days":5})
  [MCP server -> agent] {"saved":true,"reason":"2026-11-02 has the best outdoor conditions ...
```

**Demo script (no OpenAI key needed).** It calls every tool over stdio against the live API and shows the failure cases:
```bash
python examples/demo_client.py Beirut
```

**Run the server on its own** (for example, with MCP Inspector or Claude Desktop):
```bash
python weather_trip_server.py
```

## Tests

```bash
pytest -v
```
There are 13 tests. They run the tools through a real in-memory FastMCP `Client`, with the Open-Meteo API replaced by an `httpx.MockTransport`, so they run offline and give the same result every time. They cover:
- tool discovery (exactly three tools),
- a happy path for each tool,
- failure cases: unknown city, out-of-range days, service unavailable, bad date, past date, duplicate trip, all-rainy forecast, and an error passed up through the orchestration tool,
- that the Data tool never writes to storage,
- the `trips://saved` resource.

Tests set `TRIPS_FILE` to a temporary path, so they never touch `data/trips.json`.

## Example queries for the demo

1. *"What's the weather like in Paris for the next 3 days?"* → Data tool
2. *"Save a museum visit in Beirut for tomorrow."* → Action tool
3. *"Find the best day this week to go hiking in Byblos and book it."* → Orchestration tool
4. *"What's the forecast for Qwertyuiopville?"* → Data tool failure, explained by the agent
5. *"Save a beach day in Tyre on 31/12."* → Action tool rejects the bad date format, and the agent rephrases

## Project layout

```
weather_trip_server.py   FastMCP server (3 tools + 1 resource)
agent.py                 OpenAI Agents SDK host/client
examples/demo_client.py  Scripted tool walkthrough, no LLM
tests/test_server.py     pytest suite
PART1_SUMMARY.md         Part 1 written summary
REFLECTION.md            Reflection
```


