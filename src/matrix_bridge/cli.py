#!/usr/bin/env python3
"""CLI for E2EE Matrix bridge - send and read encrypted messages."""

import argparse
import asyncio
import getpass
import json
import logging
import os
import stat
import sys
from datetime import datetime, timezone

from .client import MatrixE2EEClient
from .config import Config, load_config, save_config, DEFAULT_DIR


def _configure_logging():
    """Send all library logs to stderr so stdout stays clean for output."""
    logging.basicConfig(
        stream=sys.stderr,
        level=logging.WARNING,
        format="%(message)s",
    )
    logging.getLogger("nio.crypto").setLevel(logging.CRITICAL)
    logging.getLogger("nio.responses").setLevel(logging.CRITICAL)


def main():
    _configure_logging()

    parser = argparse.ArgumentParser(
        prog="matrix-bridge",
        description="E2EE Matrix bridge for Claude Code",
    )
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    sub = parser.add_subparsers(dest="command", required=True)

    # setup
    setup_p = sub.add_parser("setup", help="First-time login and key upload")
    setup_p.add_argument("--homeserver", default="https://matrix.org")
    setup_p.add_argument("--user-id", help="e.g. @bot:matrix.org")
    setup_p.add_argument("--device-name", default="matrix-bridge")
    setup_p.add_argument("--default-room", default="", help="Default room ID")
    setup_p.add_argument("--default-mention", default="", help="Default mention user ID")

    # send
    send_p = sub.add_parser("send", help="Send a message")
    send_p.add_argument("message", help="Message text")
    send_p.add_argument("--room", help="Room ID (uses default if not set)")
    send_p.add_argument("--mention", help="User ID to mention (uses default if not set)")
    send_p.add_argument("--no-mention", action="store_true", help="Skip default mention")

    # read
    read_p = sub.add_parser("read", help="Read recent messages")
    read_p.add_argument("--room", help="Room ID (uses default if not set)")
    read_p.add_argument("--limit", type=int, default=10, choices=range(1, 101), metavar="N", help="Number of messages (1-100)")

    # rooms
    sub.add_parser("rooms", help="List joined rooms")

    # send-wait
    sw_p = sub.add_parser("send-wait", help="Send and wait for a reply")
    sw_p.add_argument("message", help="Message text")
    sw_p.add_argument("--room", help="Room ID")
    sw_p.add_argument("--mention", help="User ID to mention")
    sw_p.add_argument("--no-mention", action="store_true", help="Skip default mention")
    sw_p.add_argument("--timeout", type=int, default=30, choices=range(1, 301), metavar="N", help="Wait timeout in seconds (1-300)")

    # config
    config_p = sub.add_parser("config", help="View or update configuration")
    config_p.add_argument("key", nargs="?", help="Config key to view or set")
    config_p.add_argument("value", nargs="?", help="New value to set")

    args = parser.parse_args()
    try:
        asyncio.run(_dispatch(args))
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


async def _dispatch(args):
    if args.command == "setup":
        await _setup(args)
    elif args.command == "send":
        await _send(args)
    elif args.command == "read":
        await _read(args)
    elif args.command == "rooms":
        await _rooms(args)
    elif args.command == "send-wait":
        await _send_wait(args)
    elif args.command == "config":
        await _config(args)


def _resolve_mention(args, config):
    """Resolve the mention from args, respecting --no-mention."""
    if getattr(args, "no_mention", False):
        return None
    return args.mention or config.default_mention or None


def _require_session(client):
    """Restore session or exit with helpful message."""
    if not client.restore_session():
        print("Error: no saved session.", file=sys.stderr)
        print("Run 'matrix-bridge setup' to log in and generate encryption keys.", file=sys.stderr)
        sys.exit(1)


def _require_room(room):
    """Check room is set or exit with helpful message."""
    if not room:
        print("Error: no room specified.", file=sys.stderr)
        print("Use --room or set a default: matrix-bridge config default_room '!roomid:server'", file=sys.stderr)
        sys.exit(1)


async def _setup(args):
    user_id = args.user_id or input("User ID (e.g. @bot:matrix.org): ").strip()
    if not user_id:
        print("User ID is required.", file=sys.stderr)
        sys.exit(1)

    password = getpass.getpass("Password: ")

    config = Config(
        homeserver=args.homeserver,
        user_id=user_id,
        device_name=args.device_name,
        default_room=args.default_room,
        default_mention=args.default_mention,
    )
    config.ensure_dirs()

    client = MatrixE2EEClient(config)
    try:
        creds = await client.login_with_password(password)
        save_config(config)
        print(f"Logged in as {creds['user_id']} (device: {creds['device_id']})")
        print(f"Config saved to {config.config_file}")
        print(f"Credentials saved to {config.credentials_file}")

        # Validate default room membership
        if args.default_room:
            rooms = await client.get_rooms()
            joined_ids = {r["room_id"] for r in rooms}
            if args.default_room not in joined_ids:
                print(f"\nWarning: not a member of {args.default_room}", file=sys.stderr)
                print("Join the room in your Matrix client (Element, etc.) first.", file=sys.stderr)
    finally:
        await client.close()


async def _send(args):
    config = load_config()
    room = args.room or config.default_room
    mention = _resolve_mention(args, config)
    _require_room(room)

    client = MatrixE2EEClient(config)
    try:
        _require_session(client)
        await client.sync_once()
        event_id = await client.send_message(room, args.message, mention)
        if args.json:
            print(json.dumps({"event_id": event_id}))
        else:
            print(f"Sent: {event_id}")
    finally:
        await client.close()


async def _read(args):
    config = load_config()
    room = args.room or config.default_room
    _require_room(room)

    client = MatrixE2EEClient(config)
    try:
        _require_session(client)
        await client.sync_once()
        messages = await client.read_messages(room, args.limit)
        if args.json:
            print(json.dumps(messages, indent=2))
        else:
            for msg in messages:
                ts = datetime.fromtimestamp(msg["timestamp"] / 1000, tz=timezone.utc)
                sender = msg["sender"].split(":")[0].lstrip("@")
                flag = " [!]" if msg["decryption_failed"] else ""
                print(f"[{ts:%H:%M}] {sender}{flag}: {msg['body']}")
    finally:
        await client.close()


async def _rooms(args):
    config = load_config()
    client = MatrixE2EEClient(config)
    try:
        _require_session(client)
        await client.sync_once()
        rooms = await client.get_rooms()
        if args.json:
            print(json.dumps(rooms, indent=2))
        else:
            for r in rooms:
                enc = "E2EE" if r["encrypted"] else "plain"
                print(f"  {r['room_id']}  {r['name']}  ({enc}, {r['member_count']} members)")
    finally:
        await client.close()


async def _send_wait(args):
    config = load_config()
    room = args.room or config.default_room
    mention = _resolve_mention(args, config)
    _require_room(room)

    client = MatrixE2EEClient(config)
    try:
        _require_session(client)
        await client.sync_once()
        event_id = await client.send_message(room, args.message, mention)

        if not args.json:
            print(f"Sent: {event_id}", file=sys.stderr)
            print(f"Waiting up to {args.timeout}s for reply...", file=sys.stderr)

        my_user = client.client.user_id
        loop = asyncio.get_running_loop()
        deadline = loop.time() + args.timeout
        while loop.time() < deadline:
            await asyncio.sleep(3)
            await client.sync_once(timeout_ms=5000)
            messages = await client.read_messages(room, 20)

            idx = next((i for i, m in enumerate(messages) if m["event_id"] == event_id), -1)
            if idx == -1:
                continue
            replies = [m for m in messages[idx + 1:] if m["sender"] != my_user]
            if replies:
                if args.json:
                    print(json.dumps(replies, indent=2))
                else:
                    for msg in replies:
                        ts = datetime.fromtimestamp(msg["timestamp"] / 1000, tz=timezone.utc)
                        sender = msg["sender"].split(":")[0].lstrip("@")
                        flag = " [!]" if msg["decryption_failed"] else ""
                        print(f"[{ts:%H:%M}] {sender}{flag}: {msg['body']}")
                return

        if not args.json:
            print("No reply received within timeout.", file=sys.stderr)
        else:
            print(json.dumps([]))
    finally:
        await client.close()


async def _config(args):
    config_file = DEFAULT_DIR / "config.json"
    valid_keys = set(Config.__dataclass_fields__.keys())

    if not config_file.exists():
        print("No config found. Run 'matrix-bridge setup' first.", file=sys.stderr)
        sys.exit(1)

    config = load_config()

    # Show all config
    if not args.key:
        data = {
            "homeserver": config.homeserver,
            "user_id": config.user_id,
            "device_name": config.device_name,
            "store_path": str(config.store_path),
            "trust_mode": config.trust_mode,
            "default_room": config.default_room,
            "default_mention": config.default_mention,
        }
        if args.json:
            print(json.dumps(data, indent=2))
        else:
            for k, v in data.items():
                print(f"  {k}: {v}")
        return

    # Show single key
    if args.key and not args.value:
        if args.key not in valid_keys:
            print(f"Error: unknown key '{args.key}'. Valid keys: {', '.join(sorted(valid_keys))}", file=sys.stderr)
            sys.exit(1)
        val = getattr(config, args.key)
        print(str(val))
        return

    # Set a key
    if args.key not in valid_keys:
        print(f"Error: unknown key '{args.key}'. Valid keys: {', '.join(sorted(valid_keys))}", file=sys.stderr)
        sys.exit(1)
    setattr(config, args.key, args.value)
    save_config(config)
    print(f"Set {args.key} = {args.value}")


if __name__ == "__main__":
    main()
