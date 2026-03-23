import json
import os
import stat
import sys
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_DIR = Path("~/.matrix-bridge").expanduser()


@dataclass
class Config:
    homeserver: str = "https://matrix.org"
    user_id: str = ""
    device_name: str = "matrix-bridge"
    store_path: Path = field(default_factory=lambda: DEFAULT_DIR / "store")
    trust_mode: str = "tofu"
    default_room: str = ""
    default_mention: str = ""

    def __post_init__(self):
        if isinstance(self.store_path, str):
            self.store_path = Path(self.store_path).expanduser()
        if self.trust_mode not in ("tofu", "all", "explicit"):
            print(f"Error: trust_mode must be tofu, all, or explicit (got {self.trust_mode})", file=sys.stderr)
            sys.exit(1)

    @property
    def credentials_file(self) -> Path:
        return self.store_path / "credentials.json"

    @property
    def config_file(self) -> Path:
        return DEFAULT_DIR / "config.json"

    def ensure_dirs(self):
        self.store_path.mkdir(parents=True, exist_ok=True)
        os.chmod(self.store_path, stat.S_IRWXU)  # 0700


def load_config() -> Config:
    config_file = DEFAULT_DIR / "config.json"
    if config_file.exists():
        data = json.loads(config_file.read_text())
        return Config(**{k: v for k, v in data.items() if k in Config.__dataclass_fields__})
    return Config()


def save_config(config: Config) -> None:
    DEFAULT_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "homeserver": config.homeserver,
        "user_id": config.user_id,
        "device_name": config.device_name,
        "store_path": str(config.store_path),
        "trust_mode": config.trust_mode,
        "default_room": config.default_room,
        "default_mention": config.default_mention,
    }
    path = config.config_file
    path.write_text(json.dumps(data, indent=2))
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 0600
