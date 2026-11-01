"""OpenAI Agents SDK host that connects to the Weather & Trip Planner MCP server.

The agent is the MCP Host; MCPServerStdio is the MCP Client, which launches
weather_trip_server.py as a subprocess and talks to it over stdio.

Usage:
    python agent.py                       # interactive chat
    python agent.py "Plan a hike in Beirut this week"   # single query
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from agents import Agent, Runner
from agents.items import ToolCallItem, ToolCallOutputItem
from agents.mcp import MCPServerStdio

SERVER_PATH = Path(__file__).parent / "weather_trip_server.py"

INSTRUCTIONS = """You are a friendly weather and day-trip planning assistant.

Pick the right tool for each request:
- get_weather_forecast: the user only wants to know the weather. Never save anything.
- save_trip_plan: the user has already chosen a specific city, date and activity to save.
- plan_outdoor_trip: the user wants YOU to choose the best day for an outdoor activity and save it.

If a tool returns an error, explain it plainly to the user and suggest how to fix it.
Always mention concrete numbers (temperature, rain chance) when discussing weather.
Today's date is {today}."""


def _log_tool_activity(result) -> None:
    """Print each MCP round trip so the demo shows agent -> client -> server -> back."""
    for item in result.new_items:
        if isinstance(item, ToolCallItem):
            raw = item.raw_item
            name = getattr(raw, "name", "?")
            args = getattr(raw, "arguments", "")
            print(f"  [agent -> MCP server] call {name}({args})")
        elif isinstance(item, ToolCallOutputItem):
            out = str(item.output)
            print(f"  [MCP server -> agent] {out[:200]}{'...' if len(out) > 200 else ''}")


async def main() -> None:
    if not os.environ.get("OPENAI_API_KEY"):
        sys.exit("OPENAI_API_KEY is not set. See README.md for setup.")

    from datetime import date

    async with MCPServerStdio(
        name="weather-trip-planner",
        params={"command": sys.executable, "args": [str(SERVER_PATH)]},
        client_session_timeout_seconds=30,
        cache_tools_list=True,
    ) as server:
        tools = await server.list_tools()
        print("Connected to MCP server. Tools:", ", ".join(t.name for t in tools))

        agent_kwargs = {}
        if model := os.environ.get("OPENAI_MODEL"):
            agent_kwargs["model"] = model
        agent = Agent(
            name="Trip Planner",
            instructions=INSTRUCTIONS.format(today=date.today().isoformat()),
            mcp_servers=[server],
            **agent_kwargs,
        )

        one_shot = " ".join(sys.argv[1:]).strip()
        history = []
        while True:
            query = one_shot or input("\nYou: ").strip()
            if query.lower() in {"exit", "quit", ""}:
                break
            result = await Runner.run(agent, history + [{"role": "user", "content": query}])
            _log_tool_activity(result)
            print(f"\nAgent: {result.final_output}")
            history = result.to_input_list()
            if one_shot:
                break


if __name__ == "__main__":
    asyncio.run(main())
