"""Entry point for the basedclaude Matrix bot."""

import asyncio
import logging
import os
import sys

from .bot import BasedClaudeBot, DEFAULT_API_URL, DEFAULT_MODEL, DEFAULT_SYSTEM_PROMPT
from .config import load_config


def main():
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    api_key = os.environ.get("VENICE_API_KEY")
    if not api_key:
        print("Error: VENICE_API_KEY environment variable required", file=sys.stderr)
        sys.exit(1)

    config = load_config()
    room_id = os.environ.get("BASEDCLAUDE_ROOM", config.default_room)
    if not room_id:
        print(
            "Error: Set BASEDCLAUDE_ROOM env var or configure default_room",
            file=sys.stderr,
        )
        sys.exit(1)

    bot = BasedClaudeBot(
        room_id=room_id,
        api_key=api_key,
        api_url=os.environ.get("BASEDCLAUDE_API_URL", DEFAULT_API_URL),
        model=os.environ.get("BASEDCLAUDE_MODEL", DEFAULT_MODEL),
        system_prompt=os.environ.get("BASEDCLAUDE_SYSTEM_PROMPT", DEFAULT_SYSTEM_PROMPT),
    )

    async def run():
        try:
            await bot.start()
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            await bot.close()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
