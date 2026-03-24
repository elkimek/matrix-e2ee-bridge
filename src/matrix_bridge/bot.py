"""Always-on Matrix bot that responds to @basedclaude mentions."""

import asyncio
import logging
import time
from collections import deque

import httpx
from nio import RoomMessageText, SyncResponse

from .client import MatrixE2EEClient
from .config import load_config
from .trust import apply_trust_policy

logger = logging.getLogger("basedclaude-bot")

DEFAULT_SYSTEM_PROMPT = (
    "You are basedclaude, an AI assistant in a Matrix chat room. "
    "Be helpful, concise, and direct."
)
DEFAULT_MODEL = "claude-sonnet-4-6"
DEFAULT_API_URL = "https://api.venice.ai/api/v1"


class BasedClaudeBot:
    def __init__(
        self,
        room_id: str,
        api_key: str,
        api_url: str = DEFAULT_API_URL,
        model: str = DEFAULT_MODEL,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    ):
        self.config = load_config()
        self.matrix = MatrixE2EEClient(self.config)
        self.room_id = room_id
        self.api_key = api_key
        self.api_url = api_url
        self.model = model
        self.system_prompt = system_prompt
        self.context: deque[dict] = deque(maxlen=20)
        self._start_time_ms = 0
        self._ready = False
        self._http = httpx.AsyncClient(timeout=60)

    async def start(self):
        """Connect to Matrix and start listening forever."""
        if not self.matrix.restore_session():
            raise RuntimeError("No saved session. Run 'matrix-bridge setup' first.")

        self._start_time_ms = int(time.time() * 1000)

        # Register callbacks before syncing
        self.matrix.client.add_event_callback(self._on_message, RoomMessageText)
        self.matrix.client.add_response_callback(self._on_sync, SyncResponse)

        # Initial sync
        await self.matrix.client.sync(timeout=30000, full_state=True)
        if self.matrix.client.should_upload_keys:
            await self.matrix.client.keys_upload()
        apply_trust_policy(self.matrix.client, self.config.trust_mode)
        for room in self.matrix.client.rooms.values():
            if room.encrypted:
                room.ignore_unverified_devices = True

        self._ready = True
        logger.info(f"Bot ready — listening in {self.room_id}")

        # Block forever
        await self.matrix.client.sync_forever(timeout=30000, full_state=False)

    async def _on_sync(self, response: SyncResponse):
        """Apply trust policy after each sync."""
        apply_trust_policy(self.matrix.client, self.config.trust_mode)
        for room in self.matrix.client.rooms.values():
            if room.encrypted:
                room.ignore_unverified_devices = True

    async def _on_message(self, room, event: RoomMessageText):
        """Handle incoming messages."""
        if event.server_timestamp < self._start_time_ms:
            return
        if not self._ready:
            return
        if room.room_id != self.room_id:
            return
        if event.sender == self.config.user_id:
            return

        sender = event.sender.split(":")[0].lstrip("@")
        body = event.body
        logger.debug(f"Message from {sender}: {body[:120]}")

        # Record all messages for context
        self.context.append({"role": "user", "content": f"{sender}: {body}"})

        # Only respond to mentions
        formatted = getattr(event, "formatted_body", "") or ""
        if not self._is_mentioned(body, formatted):
            return

        logger.info(f"Mentioned by {sender}: {body[:100]}")

        try:
            response = await self._generate_response()
            await self.matrix.send_message(self.room_id, response)
            self.context.append({"role": "assistant", "content": response})
            logger.info(f"Replied ({len(response)} chars)")
        except Exception:
            logger.exception("Failed to generate/send response")

    def _is_mentioned(self, body: str, formatted_body: str = "") -> bool:
        text = f"{body} {formatted_body}".lower()
        return "basedclaude" in text

    async def _generate_response(self) -> str:
        """Call LLM API to generate a response."""
        messages = [{"role": "system", "content": self.system_prompt}]
        messages.extend(list(self.context))

        resp = await self._http.post(
            f"{self.api_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": self.model,
                "messages": messages,
                "max_tokens": 2048,
            },
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]

    async def close(self):
        await self._http.aclose()
        await self.matrix.close()
