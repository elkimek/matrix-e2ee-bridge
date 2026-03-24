"""MCP server exposing Matrix E2EE messaging as tools + channel notifications."""

import asyncio
import logging
import sys
import time
from datetime import datetime, timezone

import anyio
from mcp.server.lowlevel.server import Server
from mcp.server.stdio import stdio_server
from mcp.server.session import ServerSession
from mcp.types import (
    CallToolRequest,
    CallToolResult,
    JSONRPCMessage,
    JSONRPCNotification,
    ListToolsRequest,
    ListToolsResult,
    TextContent,
    Tool,
)
from nio import RoomMessageText

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

# Session reference — set once the client connects
_session: ServerSession | None = None
_start_time_ms = 0

TOOLS = [
    Tool(
        name="send_message",
        description="Send a message to a Matrix room. Automatically encrypted if the room has E2EE enabled.",
        inputSchema={
            "type": "object",
            "properties": {
                "room_id": {"type": "string", "description": "The Matrix room ID"},
                "message": {"type": "string", "description": "The message text to send"},
                "mention": {
                    "anyOf": [{"type": "string"}, {"type": "null"}],
                    "default": None,
                    "description": "Optional user ID to mention",
                },
            },
            "required": ["room_id", "message"],
        },
    ),
    Tool(
        name="send_and_wait",
        description="Send a message and wait for a reply.",
        inputSchema={
            "type": "object",
            "properties": {
                "room_id": {"type": "string"},
                "message": {"type": "string"},
                "mention": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None},
                "wait_for": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None},
                "timeout": {"type": "integer", "default": 30},
            },
            "required": ["room_id", "message"],
        },
    ),
    Tool(
        name="read_messages",
        description="Read recent messages from a Matrix room, decrypting E2EE messages automatically.",
        inputSchema={
            "type": "object",
            "properties": {
                "room_id": {"type": "string"},
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["room_id"],
        },
    ),
    Tool(
        name="list_rooms",
        description="List all joined Matrix rooms with their IDs, names, and encryption status.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="join_room",
        description="Join a Matrix room by ID or alias.",
        inputSchema={
            "type": "object",
            "properties": {
                "room_id": {"type": "string", "description": "Room ID or alias"},
            },
            "required": ["room_id"],
        },
    ),
]


async def handle_list_tools(_req: ListToolsRequest) -> ListToolsResult:
    return ListToolsResult(tools=TOOLS)


async def handle_call_tool(req: CallToolRequest) -> CallToolResult:
    name = req.params.name
    args = req.params.arguments or {}

    try:
        if name == "send_message":
            eid = await matrix.send_message(args["room_id"], args["message"], args.get("mention"))
            return CallToolResult(content=[TextContent(type="text", text=f"Sent (event: {eid})")])

        elif name == "send_and_wait":
            timeout = max(1, min(args.get("timeout", 30), 300))
            eid = await matrix.send_message(args["room_id"], args["message"], args.get("mention"))
            replies = await matrix.get_new_messages(args["room_id"], config.user_id, eid, timeout * 1000)
            if not replies:
                return CallToolResult(content=[TextContent(type="text", text="Message sent but no reply within timeout.")])
            return CallToolResult(content=[TextContent(type="text", text="\n\n".join(r["body"] for r in replies))])

        elif name == "read_messages":
            limit = max(1, min(args.get("limit", 20), 100))
            messages = await matrix.read_messages(args["room_id"], limit)
            if not messages:
                return CallToolResult(content=[TextContent(type="text", text="No messages in room.")])
            lines = []
            for m in messages:
                ts = datetime.fromtimestamp(m["timestamp"] / 1000, tz=timezone.utc)
                sender = m["sender"].split(":")[0].lstrip("@")
                failed = " [decryption failed]" if m["decryption_failed"] else ""
                lines.append(f"[{ts:%H:%M:%S}] {sender}: {m['body']}{failed}")
            return CallToolResult(content=[TextContent(type="text", text="\n\n".join(lines))])

        elif name == "list_rooms":
            rooms = await matrix.get_rooms()
            if not rooms:
                return CallToolResult(content=[TextContent(type="text", text="Not in any rooms.")])
            lines = [f"{r['name']} ({r['room_id']}) — {'encrypted' if r['encrypted'] else 'unencrypted'}, {r['member_count']} members" for r in rooms]
            return CallToolResult(content=[TextContent(type="text", text="\n".join(lines))])

        elif name == "join_room":
            joined_id = await matrix.join_room(args["room_id"])
            return CallToolResult(content=[TextContent(type="text", text=f"Joined room {joined_id}")])

        else:
            return CallToolResult(content=[TextContent(type="text", text=f"Unknown tool: {name}")], isError=True)

    except Exception as e:
        return CallToolResult(content=[TextContent(type="text", text=f"{name}: {e}")], isError=True)


def _is_mention(body: str, formatted_body: str = "") -> bool:
    text = f"{body} {formatted_body}".lower()
    return "basedclaude" in text


async def _on_matrix_message(room, event: RoomMessageText) -> None:
    """Push a channel notification when @basedclaude is mentioned."""
    global _session, _start_time_ms
    if _session is None:
        return
    if event.server_timestamp < _start_time_ms:
        return
    if event.sender == config.user_id:
        return

    formatted = getattr(event, "formatted_body", "") or ""
    if not _is_mention(event.body, formatted):
        return

    sender = event.sender.split(":")[0].lstrip("@")
    logger.info(f"Channel notification: mention from {sender}")

    try:
        notification = JSONRPCNotification(
            jsonrpc="2.0",
            method="notifications/claude/channel",
            params={
                "content": f"{sender}: {event.body}",
                "meta": {
                    "chat_id": room.room_id,
                    "message_id": event.event_id,
                    "user": event.sender,
                    "ts": datetime.fromtimestamp(
                        event.server_timestamp / 1000, tz=timezone.utc
                    ).isoformat(),
                },
            },
        )
        await _session._write_stream.send(JSONRPCMessage(notification))
    except Exception:
        logger.exception("Failed to send channel notification")


server = Server(
    name="matrix-bridge-mcp",
    version="0.2.0",
    instructions=(
        'Messages from the Matrix E2EE room arrive as <channel source="matrix-bridge-mcp">. '
        "Reply using the send_message tool with the room ID from the chat_id attribute."
    ),
)
server.request_handlers[ListToolsRequest] = handle_list_tools
server.request_handlers[CallToolRequest] = handle_call_tool


async def run_server() -> None:
    global _session, _start_time_ms

    matrix.restore_session()
    _start_time_ms = int(time.time() * 1000)

    # Register Matrix event callback for channel notifications
    matrix.client.add_event_callback(_on_matrix_message, RoomMessageText)

    # Start Matrix sync
    await matrix.start_sync()
    logger.info("Matrix sync started")

    init_options = server.create_initialization_options(
        experimental_capabilities={"claude/channel": {}},
    )

    try:
        async with stdio_server() as (read_stream, write_stream):
            async with anyio.create_task_group() as tg:
                async def _run_mcp():
                    global _session
                    # Inline the server.run() logic to capture the session
                    async with ServerSession(
                        read_stream, write_stream, init_options
                    ) as session:
                        _session = session
                        logger.info("MCP session established — channel active")
                        async for message in session.incoming_messages:
                            tg.start_soon(
                                server._handle_message,
                                message,
                                session,
                                {},  # lifespan_context
                                False,  # raise_exceptions
                            )

                tg.start_soon(_run_mcp)
    finally:
        await matrix.close()


def main():
    anyio.run(run_server)


if __name__ == "__main__":
    main()
