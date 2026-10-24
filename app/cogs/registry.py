"""The five cogs: code declares them, the database stores their state.

`locked=True` means the toggle refuses. Three cogs are locked because
disabling `settings` or `utility` would leave no path back, and `status` is
how an operator finds out the bot is unhealthy.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CogMeta:
    key: str
    module: str
    class_name: str
    locked: bool
    description: str
    enabled_by_default: bool = True


COGS: dict[str, CogMeta] = {
    "status": CogMeta(
        key="status",
        module="app.cogs.status",
        class_name="StatusCog",
        locked=True,
        description="Ping, latency, memory, CPU and cog health.",
    ),
    "settings": CogMeta(
        key="settings",
        module="app.cogs.settings",
        class_name="SettingsCog",
        locked=True,
        description="Cog toggles, guild config and audit.",
    ),
    "github": CogMeta(
        key="github",
        module="app.cogs.github",
        class_name="GithubCog",
        locked=False,
        description="Org and repo updates posted to a channel.",
    ),
    "dev": CogMeta(
        key="dev",
        module="app.cogs.dev",
        class_name="DevCog",
        locked=False,
        description="Announcements and nudges.",
    ),
    "utility": CogMeta(
        key="utility",
        module="app.cogs.utility",
        class_name="UtilityCog",
        locked=True,
        description="Help, about, invite.",
    ),
}

COG_NAMES: tuple[str, ...] = tuple(COGS)
TOGGLEABLE: frozenset[str] = frozenset(key for key, cog in COGS.items() if not cog.locked)
LOCKED: frozenset[str] = frozenset(key for key, cog in COGS.items() if cog.locked)

# Maps a cog class name back to its registry key for the command gate.
CLASS_TO_KEY: dict[str, str] = {cog.class_name: key for key, cog in COGS.items()}


def class_key(binding: object) -> str | None:
    """Return the registry key for a command's cog binding, if it is one of ours."""
    if binding is None:
        return None
    return CLASS_TO_KEY.get(type(binding).__name__)


def meta(key: str) -> CogMeta:
    return COGS[key]
