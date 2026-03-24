"""MCP server exposing Matrix E2EE messaging as tools."""

import logging
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from mcp.server.fastmcp import FastMCP

from .client import MatrixE2EEClient
from .config import load_config
from .trust import apply_trust_policy

logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger("matrix-bridge-mcp")

config = load_config()
matrix = MatrixE2EEClient(config)


@asynccontextmanager
async def lifespan(server):
    matrix.restore_session()
    await matrix.start_sync()
    try:
        yield {"matrix": matrix}
    finally:
        await matrix.close()


mcp = FastMCP("matrix-bridge-mcp", lifespan=lifespan)


@mcp.tool()
async def send_message(
    room_id: str,
    message: str,
    mention: str | None = None,
) -> str:
    """Send a message to a Matrix room. Automatically encrypted if the room has E2EE enabled.

    Args:
        room_id: The Matrix room ID (e.g. !abc123:matrix.org)
        message: The message text to send
        mention: Optional user ID to mention (e.g. @user:matrix.org)
    """
    event_id = await matrix.send_message(room_id, message, mention)
    return f"Sent (event: {event_id})"


@mcp.tool()
async def send_and_wait(
    room_id: str,
    message: str,
    mention: str | None = None,
    wait_for: str | None = None,
    timeout: int = 30,
) -> str:
    """Send a message and wait for a reply.

    Args:
        room_id: The Matrix room ID
        message: The message text to send
        mention: Optional user ID to mention
        wait_for: Optional user ID to wait for a reply from
        timeout: Seconds to wait for a reply (default 30)
    """
    timeout = max(1, min(timeout, 300))
    event_id = await matrix.send_message(room_id, message, mention)
    replies = await matrix.get_new_messages(
        room_id,
        config.user_id,
        event_id,
        timeout * 1000,
    )
    if not replies:
        return "Message sent but no reply within timeout. Use read_messages to check later."

    return "\n\n".join(r["body"] for r in replies)


@mcp.tool()
async def read_messages(room_id: str, limit: int = 20) -> str:
    """Read recent messages from a Matrix room, decrypting E2EE messages automatically.

    Args:
        room_id: The Matrix room ID
        limit: Number of messages to fetch (default 20)
    """
    limit = max(1, min(limit, 100))
    messages = await matrix.read_messages(room_id, limit)
    if not messages:
        return "No messages in room."

    lines = []
    for m in messages:
        ts = datetime.fromtimestamp(m["timestamp"] / 1000, tz=timezone.utc)
        time_str = ts.strftime("%H:%M:%S")
        sender = m["sender"].split(":")[0].lstrip("@")
        failed = " [decryption failed]" if m["decryption_failed"] else ""
        lines.append(f"[{time_str}] {sender}: {m['body']}{failed}")
    return "\n\n".join(lines)


@mcp.tool()
async def list_rooms() -> str:
    """List all joined Matrix rooms with their IDs, names, and encryption status."""
    rooms = await matrix.get_rooms()
    if not rooms:
        return "Not in any rooms."

    lines = []
    for r in rooms:
        enc = "encrypted" if r["encrypted"] else "unencrypted"
        lines.append(f"{r['name']} ({r['room_id']}) — {enc}, {r['member_count']} members")
    return "\n".join(lines)


@mcp.tool()
async def join_room(room_id: str) -> str:
    """Join a Matrix room by ID or alias.

    Args:
        room_id: The room ID (e.g. !abc123:matrix.org) or alias (e.g. #room:matrix.org)
    """
    joined_id = await matrix.join_room(room_id)
    return f"Joined room {joined_id}"


def main():
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
