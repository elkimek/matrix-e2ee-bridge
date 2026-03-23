import logging
from nio import AsyncClient

logger = logging.getLogger(__name__)


def apply_trust_policy(client: AsyncClient, mode: str) -> None:
    """Trust devices according to the configured policy."""
    if mode == "explicit":
        return

    trusted = 0
    for user_id, devices in client.device_store.items():
        for device_id, olm_device in devices.items():
            if not olm_device.verified:
                if mode in ("tofu", "all"):
                    client.verify_device(olm_device)
                    trusted += 1

    if trusted:
        logger.info(f"Auto-trusted {trusted} device(s)")
