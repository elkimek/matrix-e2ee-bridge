# matrix-e2ee-bridge

A CLI and MCP server for sending and reading end-to-end encrypted (E2EE) Matrix messages. Designed for bot-to-bot communication — particularly [Claude Code](https://claude.ai/claude-code) talking to OpenClaw bots via Matrix.

## Why

Matrix supports E2EE, but the plain HTTP API can't encrypt or decrypt messages. This tool wraps [matrix-nio](https://github.com/poljar/matrix-nio) with Olm/Megolm support into a simple CLI that handles session management, key exchange, and device trust automatically.

Use it to give your CLI tools, bots, or AI agents the ability to participate in encrypted Matrix rooms.

## Quick start

1. Install `libolm` on your OpenClaw server (see [prerequisites](#prerequisites))
2. Install the bridge: `pip install .` (see [install](#install))
3. Run setup: `matrix-bridge setup --user-id @yourbot:matrix.org --default-room '!roomid:matrix.org'`
4. Test it: `matrix-bridge send "hello"` and `matrix-bridge read`

## Prerequisites

- Python 3.10+
- `libolm` development headers
- A Matrix account for the bridge to use (separate from your OpenClaw bot)
- An E2EE room where both the bridge account and OpenClaw bot are members

### Install libolm

```bash
# Debian/Ubuntu
sudo apt install libolm-dev

# Fedora
sudo dnf install libolm-devel

# macOS
brew install libolm

# Arch
sudo pacman -S libolm
```

## Install

Install on the same machine as your OpenClaw instance. The bridge needs persistent storage for encryption keys, and colocating it with OpenClaw means the shortest path between Claude Code and your bot — no extra infrastructure.

```bash
git clone https://github.com/elkimek/matrix-e2ee-bridge.git
cd matrix-e2ee-bridge
python3 -m venv ~/.matrix-bridge-venv
~/.matrix-bridge-venv/bin/pip install .
```

Optional — add to PATH so you can call `matrix-bridge` directly:

```bash
sudo ln -s ~/.matrix-bridge-venv/bin/matrix-bridge /usr/local/bin/matrix-bridge
```

## Setup

Before running setup:
1. Create a Matrix account for the bridge (e.g. at [matrix.org](https://app.element.io))
2. Join the account to an E2EE room with your OpenClaw bot
3. Copy the room ID (in Element: Room Settings > Advanced > Internal room ID)

Then run setup once on the OpenClaw server:

```bash
matrix-bridge setup \
  --user-id @yourbot:matrix.org \
  --default-room '!your-e2ee-room:matrix.org' \
  --default-mention @your-openclaw-bot:matrix.org
```

You'll be prompted for the account password. After login, the password is never needed again — the tool saves an access token and encryption keys to `~/.matrix-bridge/`.

## Usage

### Send a message

```bash
matrix-bridge send "Hello from the bridge!"
```

Uses the default room and mention from config. Override with flags:

```bash
matrix-bridge send "Hello!" --room '!roomid:matrix.org' --mention @user:matrix.org
matrix-bridge send "No mention" --no-mention
```

### Read messages

```bash
matrix-bridge read
matrix-bridge read --limit 20
```

### Send and wait for a reply

```bash
matrix-bridge send-wait "Hey, are you there?" --timeout 60
```

Sends a message, then polls for replies from other users. Exits when a reply arrives or the timeout is reached.

### List rooms

```bash
matrix-bridge rooms
```

### View or update config

```bash
matrix-bridge config                              # show all settings
matrix-bridge config default_room                  # show one setting
matrix-bridge config default_room '!newroom:matrix.org'  # change a setting
```

### JSON output

All commands support `--json` for machine-readable output:

```bash
matrix-bridge --json send "test"
# {"event_id": "$abc123..."}

matrix-bridge --json read --limit 3
# [{"sender": "@user:matrix.org", "body": "hello", ...}]
```

## MCP server

The bridge includes an [MCP](https://modelcontextprotocol.io/) server that exposes Matrix messaging as tools. This lets Claude Code (or any MCP client) send and read encrypted messages directly — no SSH required.

### Install with MCP support

```bash
pip install '.[mcp]'
```

### Configure in Claude Code

Add to your `.mcp.json`:

```json
{
  "mcpServers": {
    "matrix": {
      "command": "/path/to/venv/bin/matrix-bridge-mcp"
    }
  }
}
```

### Available tools

| Tool | Description |
|------|-------------|
| `send_message` | Send a message to a room (with optional @mention) |
| `send_and_wait` | Send a message and wait for a reply |
| `read_messages` | Read recent messages, decrypting E2EE automatically |
| `list_rooms` | List joined rooms with encryption status |
| `join_room` | Join a room by ID or alias |

### Auto-connect on session start

Add a `SessionStart` hook to `.claude/settings.local.json` so Claude Code announces itself on Matrix when you start a session:

```json
{
  "hooks": {
    "SessionStart": [
      {
        "hooks": [
          {
            "type": "agent",
            "prompt": "Read the last 10 messages from Matrix room !your-room:matrix.org using mcp__matrix__read_messages, send 'I'm online' via mcp__matrix__send_message, and return a summary of recent conversation.",
            "timeout": 30,
            "statusMessage": "Going online on Matrix..."
          }
        ]
      }
    ]
  }
}
```

### SSH fallback

If MCP isn't available, Claude Code can use SSH to invoke the CLI:

```bash
ssh user@your-openclaw-server 'matrix-bridge send "message"'
ssh user@your-openclaw-server 'matrix-bridge read --limit 5'
ssh user@your-openclaw-server 'matrix-bridge send-wait "question?" --timeout 30'
```

## Configuration

Config is stored at `~/.matrix-bridge/config.json`. View or edit it with `matrix-bridge config`:

```json
{
  "homeserver": "https://matrix.org",
  "user_id": "@yourbot:matrix.org",
  "device_name": "matrix-bridge",
  "store_path": "/home/you/.matrix-bridge/store",
  "trust_mode": "tofu",
  "default_room": "!roomid:matrix.org",
  "default_mention": "@friend:matrix.org"
}
```

### Trust modes

- **tofu** (default): Trust on first use — automatically verify new devices when first seen. Recommended for bot use.
- **all**: Trust all devices unconditionally.
- **explicit**: Never auto-trust — you must verify devices manually.

## Troubleshooting

**"no saved session"** — Run `matrix-bridge setup` first.

**"pip install" fails with build errors** — Install libolm dev headers: `sudo apt install libolm-dev`

**Messages show as `[!] [encrypted - keys unavailable]`** — These are old messages from before your device was set up. The bridge can only decrypt messages sent after setup. New messages will decrypt fine.

**"Warning: not a member of ..."** — Join the room in your Matrix client (Element, Fluffychat, etc.) before using the bridge.

**Messages show "not verified" in your Matrix client** — The bridge device hasn't been cross-signed. This is cosmetic — messages are still encrypted. You can verify the device in Element: User Settings > Sessions.

## Security notes

- Credentials and encryption keys are stored in `~/.matrix-bridge/` with restrictive permissions (0600/0700).
- The encryption store (`~/.matrix-bridge/store/`) contains Olm session keys. If lost, past messages encrypted with those sessions become permanently undecryptable. **Back up this directory.**
- `ignore_unverified_devices` is enabled on encrypted rooms to allow sending when new devices appear between sync and send. This is standard for bot clients.
- The tool uses TOFU (trust on first use) by default, which is appropriate for bot-to-bot communication but may not meet the security requirements of high-sensitivity environments.

## License

GPL-3.0-or-later
