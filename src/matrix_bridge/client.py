import json
import logging
import os
import stat
from html import escape as html_escape

from nio import (
    AsyncClient,
    AsyncClientConfig,
    LoginResponse,
    RoomMessageText,
    MegolmEvent,
    RoomMessagesResponse,
    KeysUploadResponse,
)

from .config import Config
from .trust import apply_trust_policy

logger = logging.getLogger(__name__)


class MatrixE2EEClient:
    def __init__(self, config: Config):
        self.config = config
        config.ensure_dirs()
        self._has_synced = False

        client_config = AsyncClientConfig(
            store_sync_tokens=True,
            encryption_enabled=True,
        )
        self.client = AsyncClient(
            homeserver=config.homeserver,
            user=config.user_id,
            store_path=str(config.store_path),
            config=client_config,
        )

    async def login_with_password(self, password: str) -> dict:
        """Login with password, upload E2EE keys, save credentials. Returns creds dict."""
        resp = await self.client.login(
            password=password,
            device_name=self.config.device_name,
        )
        if not isinstance(resp, LoginResponse):
            raise RuntimeError(f"Login failed: {resp}")

        # Initial sync to upload device keys
        await self.client.sync(timeout=30000, full_state=True)

        # Upload keys explicitly
        if self.client.should_upload_keys:
            key_resp = await self.client.keys_upload()
            if not isinstance(key_resp, KeysUploadResponse):
                logger.warning(f"Key upload issue: {key_resp}")

        # Trust all known devices
        apply_trust_policy(self.client, self.config.trust_mode)

        creds = self._save_credentials()
        logger.info(f"Logged in as {resp.user_id} (device {resp.device_id})")
        return creds

    def restore_session(self) -> bool:
        """Restore a saved session. Returns True if successful."""
        creds_file = self.config.credentials_file
        if not creds_file.exists():
            return False

        creds = json.loads(creds_file.read_text())
        self.client.access_token = creds["access_token"]
        self.client.user_id = creds["user_id"]
        self.client.device_id = creds["device_id"]
        self.client.load_store()
        logger.info(f"Restored session for {creds['user_id']} (device {creds['device_id']})")
        return True

    def _save_credentials(self) -> dict:
        creds = {
            "access_token": self.client.access_token,
            "user_id": self.client.user_id,
            "device_id": self.client.device_id,
        }
        path = self.config.credentials_file
        path.write_text(json.dumps(creds, indent=2))
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 0600
        return creds

    async def sync_once(self, timeout_ms: int = 10000) -> None:
        """Do a single sync to receive keys and update room state."""
        if not self._has_synced:
            await self.client.sync(timeout=30000, full_state=True)
            self._has_synced = True

            if self.client.should_upload_keys:
                await self.client.keys_upload()
        else:
            await self.client.sync(timeout=timeout_ms)

        apply_trust_policy(self.client, self.config.trust_mode)

        # Allow sending to devices that appear between trust and send
        for room in self.client.rooms.values():
            if room.encrypted:
                room.ignore_unverified_devices = True

    async def send_message(self, room_id: str, text: str, mention: str | None = None) -> str:
        """Send an encrypted message. Returns event_id."""
        content: dict = {"msgtype": "m.text", "body": text}

        if mention:
            local = mention.replace("@", "").split(":")[0]
            content["body"] = f"@{local} {text}"
            content["format"] = "org.matrix.custom.html"
            content["formatted_body"] = (
                f'<a href="https://matrix.to/#/{html_escape(mention)}">'
                f"{html_escape(mention)}</a> {html_escape(text)}"
            )

        resp = await self.client.room_send(
            room_id=room_id,
            message_type="m.room.message",
            content=content,
        )
        if hasattr(resp, "event_id"):
            return resp.event_id
        raise RuntimeError(f"Failed to send: {resp}")

    async def read_messages(self, room_id: str, limit: int = 10) -> list[dict]:
        """Read recent messages, decrypting where possible."""
        resp = await self.client.room_messages(
            room_id=room_id,
            start="",
            limit=limit,
        )
        if not isinstance(resp, RoomMessagesResponse):
            raise RuntimeError(f"Failed to fetch messages: {resp}")

        messages = []
        for event in reversed(resp.chunk):
            if isinstance(event, RoomMessageText):
                messages.append({
                    "sender": event.sender,
                    "body": event.body,
                    "timestamp": event.server_timestamp,
                    "event_id": event.event_id,
                    "decryption_failed": False,
                })
            elif isinstance(event, MegolmEvent):
                messages.append({
                    "sender": event.sender,
                    "body": "[encrypted - keys unavailable]",
                    "timestamp": event.server_timestamp,
                    "event_id": event.event_id,
                    "decryption_failed": True,
                })
        return messages

    async def get_rooms(self) -> list[dict]:
        """List joined rooms."""
        rooms = []
        for room_id, room in self.client.rooms.items():
            rooms.append({
                "room_id": room_id,
                "name": room.display_name or room.name or room_id,
                "encrypted": room.encrypted,
                "member_count": room.member_count,
            })
        return rooms

    async def close(self) -> None:
        await self.client.close()
