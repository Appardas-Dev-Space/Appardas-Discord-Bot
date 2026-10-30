# Appardas · Discord Bot & Management Console

Appardas is an exclusive, single-process operational Discord bot featuring an integrated web management console, GitHub repository watches, scheduled announcements, operator nudges, server settings, and live status telemetry.

---

## Features

- **Integrated Web Console**: Single-port HTTP service providing:
  - Public landing page with live 24h heartbeat telemetry and system verdicts.
  - Web management dashboard for configuring cogs and server settings per guild.
  - Operator credential authentication with mandatory initial password reset.
  - Dual-layer audit logging (server-facing audit log and operator activity log).
- **Hybrid Discord Commands**: All user-facing commands work seamlessly via slash commands (`/`) and custom server text prefixes (e.g., `!help`).
- **Rich Announcements**:
  - Beautiful embed formatting with hero banner images, author attribution, and footers.
  - Automatic image optimization (resizing and compression) for fast delivery.
  - Pre-packaged branded fallback banner (`appardas-default.png`) when no image is attached.
  - Instant and scheduled announcement delivery.
- **GitHub Watches**: Lightweight repository polling for releases and updates without third-party webhooks.
- **Operator Nudges**: Cooldown-governed operator-to-member notices with rate limiting and recipient opt-out safeguards.
- **Resilient Storage**: Single-file SQLite database with zero schema migration overhead, atomic transactions, and automated backup commands.

---

## Architecture & Cogs

Appardas runs five modular cogs inside a single process:

1. **`status`**: Heartbeat recording, connection health, REST latency, and system snapshot telemetry.
2. **`settings`**: Guild-level prefix configuration, announcement/log channel assignments, timezone selection, cog toggles, and audit logs.
3. **`github`**: Repository polling, release delivery, and watch management.
4. **`dev`**: Instant and scheduled server announcements, scheduled job execution, and operator nudges.
5. **`utility`**: Interactive help documentation, ping diagnostics, and operational guides.

---

## Prerequisites

- **Python**: 3.10 or higher.
- **Discord Bot Application**: Create a bot in the [Discord Developer Portal](https://discord.com/developers/applications).
  - **Required Privileged Intents**:
    - **Message Content Intent**: Required for custom text command prefixes (e.g. `!`).
    - **Server Members Intent**: Required for member caching and permission checks.

---

## Installation

1. **Clone the repository**:
   ```bash
   git clone <repo-url>
   cd Appardas-Discord-Bot
   ```

2. **Create and activate a virtual environment**:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

3. **Install dependencies**:
   ```bash
   pip install -r requirements.txt
   ```
   *(For testing and development, install `requirements-dev.txt`)*

4. **Configure environment variables**:
   ```bash
   cp .env.example .env
   ```
   Fill in your bot credentials in `.env`:
   - `DISCORD_TOKEN`: Your Discord bot token.
   - `BOT_OWNER_IDS`: Comma-separated Discord user IDs of the bot owners.
   - `ENABLE_MEMBER_INTENT=true`
   - `ENABLE_MESSAGE_CONTENT_INTENT=true`
   - `HTTP_PORT=8080`

---

## Running the Bot

Start the bot and embedded web server:
```bash
python -m app.main
```

Once running:
- The bot connects to Discord gateway and synchronizes slash commands.
- The web console is accessible at `http://localhost:8080`.
- Initial admin credentials:
  - **Username**: `appardas-admin`
  - **Password**: `admin` *(you will be prompted to change it upon first login)*.

---

## Sub-Account Access

Console sign-in uses **username and password only**. A Discord ID associates an account with a Discord user; it is not a login credential. Discord OAuth credentials are not required, including in production.

- An owner opens **Accounts → New account** and provides a Discord ID, unique username, temporary password, display name, role, and any server grants.
- Clicking **Create account** makes the bot DM the saved username, temporary password, and `/login` link to **the new account's Discord user ID, not the admin's ID**. The user must change the temporary password before accessing the console.
- If DM delivery cannot be confirmed, the admin receives a warning and the account remains created. Share the credentials confidentially if needed; do not create a duplicate account. Automatic DMs apply only to new account creation, not credential resets.
- For an existing Discord-ID-only account, open its details and use **Assign credentials**. Do not create another account.
- **Reset credentials** replaces local credentials and signs out existing sessions without changing the account's Discord association, role, status, or grants.
- An owner can click **Delete account** in the Accounts list or account details, enter the modal's randomly generated characters exactly, and press **Confirm deletion**. Confirmation expires after five minutes. Deletion removes credentials, sessions, and server grants while retaining activity history.
- **Appardas Admin** (`appardas-admin`) cannot be deleted, even if its display name changes. Owners managed by `BOT_OWNER_IDS` retain their existing system-account protections.

Schema v4 automatically migrates account Discord IDs to text and enforces case-insensitive username uniqueness at startup. Back up the database before deploying; pre-existing username collisions must be reconciled explicitly.

See [the authentication implementation report](docs/discord-sub-account-auth-fix.md) and [the credential DM report](docs/discord-sub-account-credential-dm.md).

---

## Testing & Quality Assurance

Run the automated test suite:
```bash
pytest
```

Verify command manifest synchronization (zero command drift):
```bash
python tools/dump_commands.py --check
```

---

## License

Private & Exclusive to Appardas. All rights reserved.
