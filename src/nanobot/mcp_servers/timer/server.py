from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("nanobot-timer")


def _resolve_timezone(timezone_name: str | None) -> ZoneInfo:
    if not timezone_name:
        return ZoneInfo("UTC")
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(
            f"Unknown timezone '{timezone_name}'. Use an IANA timezone like 'UTC' or 'Asia/Jakarta'."
        ) from exc


@mcp.tool()
def time_now(timezone_name: str | None = "UTC") -> dict[str, str]:
    """Get current date-time in ISO format for a timezone."""
    tz = _resolve_timezone(timezone_name)
    now = datetime.now(tz)
    return {
        "timezone": str(tz),
        "iso": now.isoformat(),
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H:%M:%S"),
        "weekday": now.strftime("%A"),
    }


@mcp.tool()
def time_epoch() -> dict[str, int]:
    """Get current Unix epoch timestamp in seconds."""
    now_utc = datetime.now(timezone.utc)
    return {"epoch_seconds": int(now_utc.timestamp())}


if __name__ == "__main__":
    mcp.run()
