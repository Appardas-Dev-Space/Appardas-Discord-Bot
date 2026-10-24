"""Environment parsing with one aggregated fail-fast error."""

from __future__ import annotations

import os
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

ACTIVITY_TYPES = ("playing", "watching", "listening", "competing")
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
ENVIRONMENTS = ("production", "development")
log = logging.getLogger(__name__)


class ConfigError(RuntimeError):
    """Raised once, with every configuration problem in the message."""


@dataclass(frozen=True, slots=True)
class Settings:
    # Discord
    discord_token: str
    owner_ids: frozenset[int]
    dev_guild_id: int | None
    command_sync_on_start: bool
    enable_member_intent: bool
    enable_message_content_intent: bool
    activity_type: str
    activity_text: str

    # HTTP
    http_host: str
    http_port: int
    public_base_url: str
    discord_client_id: str
    discord_client_secret: str
    session_idle_days: int
    session_max_days: int
    cookie_secure: bool
    trust_proxy: bool
    rate_limit_per_min: int
    rate_limit_burst: int
    max_connections: int
    request_timeout_s: float

    # GitHub
    github_token: str
    github_poll_interval_s: int
    github_max_watches: int
    github_request_timeout_s: float

    # Storage & logging
    database_path: Path
    log_dir: Path
    log_level: str
    log_max_bytes: int
    log_backups: int

    # Behaviour
    default_timezone: str
    nudge_enabled: bool
    nudge_pair_cooldown_s: int
    nudge_max_per_hour: int
    nudge_max_per_target_day: int
    announce_max_pending: int
    announce_max_per_poll: int
    audit_retention_days: int
    activity_retention_days: int
    nudge_retention_days: int

    # Runtime
    environment: str
    timezone: str

    @property
    def production(self) -> bool:
        return self.environment == "production"

    @property
    def display_base_url(self) -> str:
        """The single canonical origin, also used for Discord links."""
        return self.public_base_url

    @property
    def oauth_configured(self) -> bool:
        return bool(self.discord_client_id and self.discord_client_secret)

    @property
    def oauth_redirect_uri(self) -> str:
        return f"{self.public_base_url}/auth/callback"


def _raw(env: Mapping[str, str], key: str) -> str:
    return (env.get(key) or "").strip()


def _as_bool(value: str, key: str, errors: list[str], default: bool) -> bool:
    if not value:
        return default
    lowered = value.lower()
    if lowered in ("1", "true", "yes", "on"):
        return True
    if lowered in ("0", "false", "no", "off"):
        return False
    errors.append(f"{key}: expected true/false, got {value!r}")
    return default


def _as_int(
    value: str,
    key: str,
    errors: list[str],
    default: int,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    if not value:
        return default
    try:
        parsed = int(value)
    except ValueError:
        errors.append(f"{key}: expected an integer, got {value!r}")
        return default
    if minimum is not None and parsed < minimum:
        errors.append(f"{key}: must be >= {minimum}, got {parsed}")
    if maximum is not None and parsed > maximum:
        errors.append(f"{key}: must be <= {maximum}, got {parsed}")
    return parsed


def _as_float(
    value: str,
    key: str,
    errors: list[str],
    default: float,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    if not value:
        return default
    try:
        parsed = float(value)
    except ValueError:
        errors.append(f"{key}: expected a number, got {value!r}")
        return default
    if minimum is not None and parsed < minimum:
        errors.append(f"{key}: must be >= {minimum}, got {parsed}")
    if maximum is not None and parsed > maximum:
        errors.append(f"{key}: must be <= {maximum}, got {parsed}")
    return parsed


def _as_path(value: str, key: str, errors: list[str], default: Path) -> Path:
    if not value:
        return default
    path = Path(value)
    if not path.is_absolute():
        path = BASE_DIR / path
    return path


def _public_url(value: str, errors: list[str], *, production: bool, secure: bool) -> str:
    try:
        parsed = urlsplit(value)
        _ = parsed.port
        valid = (
            parsed.scheme in ("http", "https") and parsed.hostname
            and not parsed.username and not parsed.password
            and not parsed.path and not parsed.query and not parsed.fragment
            and not any(char.isspace() or ord(char) < 32 for char in value)
            and "\\" not in value
        )
    except ValueError:
        valid = False
        parsed = urlsplit("")
    if not valid:
        errors.append("PUBLIC_BASE_URL: must be an origin with no trailing slash, path, query, or credentials")
    local = parsed.hostname in ("localhost", "127.0.0.1", "::1")
    if production and (parsed.scheme != "https" or local):
        errors.append("PUBLIC_BASE_URL: production requires a public HTTPS hostname")
    if parsed.scheme == "http" and (production or not local or secure):
        errors.append("PUBLIC_BASE_URL: HTTP is allowed only for localhost with COOKIE_SECURE=false in development")
    if not secure and (production or not local):
        errors.append("COOKIE_SECURE: false is allowed only for localhost in development")
    return value


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    """Load `.env` then the process environment. Raises ConfigError once."""
    if env is None:
        load_dotenv(BASE_DIR / ".env", override=False)
        env = os.environ

    errors: list[str] = []

    discord_token = _raw(env, "DISCORD_TOKEN")
    if not discord_token:
        errors.append("DISCORD_TOKEN: required (Discord Developer Portal → Bot)")

    owner_ids: set[int] = set()
    owners_raw = _raw(env, "BOT_OWNER_IDS")
    if not owners_raw:
        errors.append("BOT_OWNER_IDS: required (comma-separated Discord user IDs)")
    else:
        for chunk in owners_raw.replace(" ", "").split(","):
            if not chunk:
                continue
            try:
                owner_id = int(chunk)
                if not 0 < owner_id <= 2**63 - 1:
                    raise ValueError
                owner_ids.add(owner_id)
            except ValueError:
                errors.append(f"BOT_OWNER_IDS: {chunk!r} is not a user ID")
        if not owner_ids:
            errors.append("BOT_OWNER_IDS: must contain at least one valid user ID")

    dev_guild_raw = _raw(env, "DISCORD_DEV_GUILD_ID")
    dev_guild_id = None
    if dev_guild_raw:
        try:
            dev_guild_id = int(dev_guild_raw)
        except ValueError:
            errors.append(f"DISCORD_DEV_GUILD_ID: expected a guild ID, got {dev_guild_raw!r}")

    # PORT or Pterodactyl's SERVER_PORT wins over HTTP_PORT which wins over 8080.
    port_raw = _raw(env, "PORT") or _raw(env, "SERVER_PORT") or _raw(env, "HTTP_PORT")
    http_port = _as_int(port_raw, "PORT/SERVER_PORT/HTTP_PORT", errors, 8080, minimum=1, maximum=65535)

    http_host = _raw(env, "HTTP_HOST") or "0.0.0.0"
    if http_host not in ("0.0.0.0", "127.0.0.1", "::", "::1", "localhost"):
        errors.append(f"HTTP_HOST: expected a bind address, got {http_host!r}")

    default_timezone = _raw(env, "DEFAULT_TIMEZONE") or "UTC"
    timezone = _raw(env, "TZ") or "UTC"
    for key, tz in (("DEFAULT_TIMEZONE", default_timezone), ("TZ", timezone)):
        try:
            ZoneInfo(tz)
        except (ZoneInfoNotFoundError, ValueError):
            errors.append(f"{key}: unknown IANA timezone {tz!r}")

    environment = (_raw(env, "ENVIRONMENT") or "production").lower()
    if environment not in ENVIRONMENTS:
        errors.append(f"ENVIRONMENT: expected one of {ENVIRONMENTS}, got {environment!r}")

    production = environment == "production"
    cookie_secure = _as_bool(_raw(env, "COOKIE_SECURE"), "COOKIE_SECURE", errors, production)
    trust_proxy = _as_bool(_raw(env, "TRUST_PROXY"), "TRUST_PROXY", errors, False)
    public_base_url = _raw(env, "PUBLIC_BASE_URL")
    if not public_base_url:
        if production:
            errors.append("PUBLIC_BASE_URL: required in production")
        public_base_url = f"{'https' if cookie_secure else 'http'}://localhost:{http_port}"
    public_base_url = _public_url(public_base_url, errors, production=production, secure=cookie_secure)
    discord_client_id = _raw(env, "DISCORD_CLIENT_ID")
    discord_client_secret = _raw(env, "DISCORD_CLIENT_SECRET")
    if discord_client_id and (not discord_client_id.isascii() or not discord_client_id.isdigit()):
        errors.append("DISCORD_CLIENT_ID: expected the numeric application ID")
    if production:
        if not discord_client_id:
            errors.append("DISCORD_CLIENT_ID: required in production")
        if not discord_client_secret:
            errors.append("DISCORD_CLIENT_SECRET: required in production")
    session_idle_days = _as_int(_raw(env, "SESSION_IDLE_DAYS"), "SESSION_IDLE_DAYS", errors, 30, minimum=1, maximum=365)
    session_max_days = _as_int(_raw(env, "SESSION_MAX_DAYS"), "SESSION_MAX_DAYS", errors, 90, minimum=1, maximum=3650)
    if session_idle_days > session_max_days:
        errors.append("SESSION_IDLE_DAYS: must be <= SESSION_MAX_DAYS")

    activity_type = (_raw(env, "BOT_ACTIVITY_TYPE") or "watching").lower()
    if activity_type not in ACTIVITY_TYPES:
        errors.append(f"BOT_ACTIVITY_TYPE: expected one of {ACTIVITY_TYPES}, got {activity_type!r}")

    log_level = (_raw(env, "LOG_LEVEL") or "INFO").upper()
    if log_level not in LOG_LEVELS:
        errors.append(f"LOG_LEVEL: expected one of {LOG_LEVELS}, got {log_level!r}")

    github_poll_interval_s = _as_int(
        _raw(env, "GITHUB_POLL_INTERVAL_S"),
        "GITHUB_POLL_INTERVAL_S",
        errors,
        300,
        minimum=120,
    )

    command_sync_on_start = _as_bool(
        _raw(env, "COMMAND_SYNC_ON_START"), "COMMAND_SYNC_ON_START", errors, True
    )
    enable_member_intent = _as_bool(
        _raw(env, "ENABLE_MEMBER_INTENT"), "ENABLE_MEMBER_INTENT", errors, True
    )
    enable_message_content_intent = _as_bool(
        _raw(env, "ENABLE_MESSAGE_CONTENT_INTENT"), "ENABLE_MESSAGE_CONTENT_INTENT", errors, False
    )

    settings = Settings(
        discord_token=discord_token,
        owner_ids=frozenset(owner_ids),
        dev_guild_id=dev_guild_id,
        command_sync_on_start=command_sync_on_start,
        enable_member_intent=enable_member_intent,
        enable_message_content_intent=enable_message_content_intent,
        activity_type=activity_type,
        activity_text=_raw(env, "BOT_ACTIVITY_TEXT") or "/help",
        http_host=http_host,
        http_port=http_port,
        public_base_url=public_base_url,
        discord_client_id=discord_client_id,
        discord_client_secret=discord_client_secret,
        session_idle_days=session_idle_days,
        session_max_days=session_max_days,
        cookie_secure=cookie_secure,
        trust_proxy=trust_proxy,
        rate_limit_per_min=_as_int(
            _raw(env, "RATE_LIMIT_PER_MIN"), "RATE_LIMIT_PER_MIN", errors, 120, minimum=1
        ),
        rate_limit_burst=_as_int(
            _raw(env, "RATE_LIMIT_BURST"), "RATE_LIMIT_BURST", errors, 30, minimum=1
        ),
        max_connections=_as_int(
            _raw(env, "MAX_CONNECTIONS"), "MAX_CONNECTIONS", errors, 32, minimum=1, maximum=512
        ),
        request_timeout_s=_as_float(
            _raw(env, "REQUEST_TIMEOUT_S"),
            "REQUEST_TIMEOUT_S",
            errors,
            10.0,
            minimum=1.0,
            maximum=120.0,
        ),
        github_token=_raw(env, "GITHUB_TOKEN"),
        github_poll_interval_s=github_poll_interval_s,
        github_max_watches=_as_int(
            _raw(env, "GITHUB_MAX_WATCHES"), "GITHUB_MAX_WATCHES", errors, 5, minimum=1, maximum=20
        ),
        github_request_timeout_s=_as_float(
            _raw(env, "GITHUB_REQUEST_TIMEOUT_S"),
            "GITHUB_REQUEST_TIMEOUT_S",
            errors,
            10.0,
            minimum=1.0,
            maximum=60.0,
        ),
        database_path=_as_path(
            _raw(env, "DATABASE_PATH"), "DATABASE_PATH", errors, BASE_DIR / "data/appardas.db"
        ),
        log_dir=_as_path(
            _raw(env, "LOG_DIR"), "LOG_DIR", errors, BASE_DIR / "data/logs"
        ),
        log_level=log_level,
        log_max_bytes=_as_int(
            _raw(env, "LOG_MAX_BYTES"), "LOG_MAX_BYTES", errors, 5_000_000, minimum=1024
        ),
        log_backups=_as_int(
            _raw(env, "LOG_BACKUPS"), "LOG_BACKUPS", errors, 2, minimum=0, maximum=10
        ),
        default_timezone=default_timezone,
        nudge_enabled=_as_bool(
            _raw(env, "NUDGE_ENABLED"), "NUDGE_ENABLED", errors, True
        ),
        nudge_pair_cooldown_s=_as_int(
            _raw(env, "NUDGE_PAIR_COOLDOWN_S"), "NUDGE_PAIR_COOLDOWN_S", errors, 60, minimum=0
        ),
        nudge_max_per_hour=_as_int(
            _raw(env, "NUDGE_MAX_PER_HOUR"), "NUDGE_MAX_PER_HOUR", errors, 5, minimum=0
        ),
        nudge_max_per_target_day=_as_int(
            _raw(env, "NUDGE_MAX_PER_TARGET_DAY"),
            "NUDGE_MAX_PER_TARGET_DAY",
            errors,
            10,
            minimum=0,
        ),
        announce_max_pending=_as_int(
            _raw(env, "ANNOUNCE_MAX_PENDING"), "ANNOUNCE_MAX_PENDING", errors, 25, minimum=1
        ),
        announce_max_per_poll=_as_int(
            _raw(env, "ANNOUNCE_MAX_PER_POLL"), "ANNOUNCE_MAX_PER_POLL", errors, 5, minimum=1
        ),
        audit_retention_days=_as_int(
            _raw(env, "AUDIT_RETENTION_DAYS"), "AUDIT_RETENTION_DAYS", errors, 90, minimum=1
        ),
        activity_retention_days=_as_int(
            _raw(env, "ACTIVITY_RETENTION_DAYS"), "ACTIVITY_RETENTION_DAYS", errors, 180, minimum=30, maximum=3650
        ),
        nudge_retention_days=_as_int(
            _raw(env, "NUDGE_RETENTION_DAYS"), "NUDGE_RETENTION_DAYS", errors, 180, minimum=1
        ),
        environment=environment,
        timezone=timezone,
    )

    if errors:
        joined = "\n".join(f"  · {line}" for line in errors)
        raise ConfigError(f"Invalid configuration ({len(errors)} problem(s)):\n{joined}")

    if not cookie_secure:
        log.warning("COOKIE_SECURE=false — session cookies are for local development only")
        if trust_proxy:
            log.warning("TRUST_PROXY=true with COOKIE_SECURE=false — proxy deployments should use HTTPS")

    return settings
