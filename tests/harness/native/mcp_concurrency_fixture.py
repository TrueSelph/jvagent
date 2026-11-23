"""Read-only synthetic records with an external audit of actual tool overlap."""

import asyncio
import json
import os
import time
from pathlib import Path

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

server = MCPServer("JV native concurrency fixture")


@server.tool(annotations=ToolAnnotations(readOnlyHint=True))
async def lookup_record(reference: str) -> dict[str, str]:
    """Read ALPHA, BETA or GAMMA and return its synthetic room and start time."""
    records = {
        "ALPHA": ("Cedar", "14:30"),
        "BETA": ("Maple", "15:00"),
        "GAMMA": ("Oak", "15:30"),
    }
    if reference not in records:
        raise ValueError("Unknown synthetic reference")

    def audit(event: str) -> None:
        with Path(os.environ["JV_MCP_FIXTURE_AUDIT"]).open("a") as stream:
            stream.write(
                json.dumps(
                    {
                        "event": event,
                        "reference": reference,
                        "pid": os.getpid(),
                        "monotonic": time.monotonic(),
                    }
                )
                + "\n"
            )

    audit("start")
    try:
        await asyncio.sleep(0.35)
        room, start = records[reference]
        return {"reference": reference, "room": room, "start": start}
    finally:
        audit("end")


if __name__ == "__main__":
    server.run(transport="stdio")
