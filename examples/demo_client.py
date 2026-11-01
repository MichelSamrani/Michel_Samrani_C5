"""Interactive-style script that exercises every tool over stdio, no LLM needed.

Launches weather_trip_server.py as a subprocess (exactly like the agent does),
calls each tool against the live Open-Meteo API, and shows the failure cases.

    python examples/demo_client.py [city]
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

from fastmcp import Client
from fastmcp.client.transports import PythonStdioTransport
from fastmcp.exceptions import ToolError

SERVER = Path(__file__).resolve().parent.parent / "weather_trip_server.py"


def show(title: str, payload) -> None:
    print(f"\n=== {title} ===")
    print(json.dumps(payload, indent=2) if not isinstance(payload, str) else payload)


async def call(client: Client, tool: str, args: dict) -> None:
    try:
        res = await client.call_tool(tool, args)
        show(f"{tool}({args})", res.structured_content)
    except ToolError as exc:
        show(f"{tool}({args})  -> ERROR (expected)", str(exc))


async def main(city: str) -> None:
    # Use a throwaway trips file so the demo never pollutes real saved data.
    trips_file = Path(tempfile.gettempdir()) / "weather_trip_demo_trips.json"
    trips_file.unlink(missing_ok=True)
    transport = PythonStdioTransport(
        script_path=str(SERVER), python_cmd=sys.executable, env={**os.environ, "TRIPS_FILE": str(trips_file)}
    )
    async with Client(transport) as client:
        show("tools", [t.name for t in await client.list_tools()])

        await call(client, "get_weather_forecast", {"city": city, "days": 3})                   # Data
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        await call(client, "save_trip_plan", {"city": city, "date": tomorrow, "activity": "museum visit"})  # Action
        await call(client, "plan_outdoor_trip", {"city": city, "activity": "hiking", "days": 5})  # Orchestration

        contents = await client.read_resource("trips://saved")
        show("resource trips://saved", contents[0].text)

        # Failure cases
        await call(client, "get_weather_forecast", {"city": "Qwertyuiopville"})
        await call(client, "get_weather_forecast", {"city": city, "days": 30})
        await call(client, "save_trip_plan", {"city": city, "date": "next friday", "activity": "hiking"})


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "Beirut"))
