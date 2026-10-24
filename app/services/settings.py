"""Cog state, guild config and the 60-second cache in front of them.

One rule matters more than the code: turning a cog off is a pause, never a
delete. No rows are removed, no loops are cancelled, no scheduled work is
dropped.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from dataclasses import dataclass, replace
from typing import Any
from zoneinfo import ZoneInfo

from ..config import Settings
from ..cogs.registry import COGS, LOCKED, TOGGLEABLE
from ..db import Database
from ..prefixes import validate_prefix
from ..util import iso, utcnow
from .audit import AuditService

log = logging.getLogger(__name__)

CACHE_TTL_S = 60
CONFIG_KEYS = ("announce_channel", "github_channel", "mod_log", "timezone", "prefix")
CHANNEL_KEYS = {"announce_channel", "github_channel", "mod_log"}


class LockedCogError(ValueError):
    pass


class UnknownCogError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class CogState:
    cog: str
    enabled: bool
    reason: str | None
    updated_at: str | None
    updated_by: int | None
    state: str  # online | offline | faulted
    audit_id: int | None = None
    held_announcements: int = 0

    @property
    def locked(self) -> bool:
        return self.cog in LOCKED


class SettingsService:
    def __init__(
        self,
        db: Database,
        config: Settings,
        audit: AuditService,
        faults: dict[str, str] | None = None,
        event_bus: Any | None = None,
    ) -> None:
        self.db = db
        self.config = config
        self.audit = audit
        self.faults = faults if faults is not None else {}
        self.bus = event_bus
        self._cache: dict[tuple[int, str], tuple[float, CogState]] = {}
        self._settings_cache: dict[int, tuple[float, dict[str, Any]]] = {}

    # --------------------------------------------------------------- guilds
    async def _execute(self, sql: str, params: tuple, conn: sqlite3.Connection | None = None) -> sqlite3.Cursor:
        return await self.db.execute_on(conn, sql, params) if conn is not None else await self.db.execute(sql, params)

    async def _fetchone(self, sql: str, params: tuple, conn: sqlite3.Connection | None = None) -> dict[str, Any] | None:
        return await self.db.fetchone_on(conn, sql, params) if conn is not None else await self.db.fetchone(sql, params)

    async def ensure_guild(self, guild_id: int, name: str | None = None, *, conn: sqlite3.Connection | None = None) -> None:
        now = iso()
        await self._execute(
            "INSERT INTO guilds (id, name, joined_at) VALUES (?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET name = COALESCE(excluded.name, guilds.name), "
            "left_at = CASE WHEN excluded.name IS NOT NULL THEN NULL ELSE guilds.left_at END",
            (guild_id, name, now), conn,
        )
        await self._execute(
            "INSERT INTO guild_settings (guild_id, timezone, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(guild_id) DO NOTHING",
            (guild_id, self.config.default_timezone, now), conn,
        )
        for cog in COGS:
            await self._execute(
                "INSERT INTO cog_state (guild_id, cog, enabled, updated_at) VALUES (?, ?, 1, ?) "
                "ON CONFLICT(guild_id, cog) DO NOTHING",
                (guild_id, cog, now), conn,
            )

    async def forget_guild(self, guild_id: int) -> None:
        await self.db.execute(
            "UPDATE guilds SET left_at = ? WHERE id = ?", (iso(), guild_id)
        )
        self.invalidate(guild_id)

    # ------------------------------------------------------------ cog state
    def invalidate(self, guild_id: int | None = None, cog: str | None = None) -> None:
        if guild_id is None:
            self._cache.clear()
            self._settings_cache.clear()
            return
        for key in [k for k in self._cache if k[0] == guild_id and (cog is None or k[1] == cog)]:
            self._cache.pop(key, None)
        self._settings_cache.pop(guild_id, None)

    def set_fault(self, cog: str, error_type: str) -> None:
        self.faults[cog] = error_type
        self.invalidate(cog=cog)

    def clear_fault(self, cog: str) -> None:
        self.faults.pop(cog, None)
        self.invalidate(cog=cog)

    def _state_from_row(
        self, cog: str, row: dict[str, Any] | None
    ) -> CogState:
        enabled = True if row is None else bool(row["enabled"])
        reason = None if row is None else row.get("reason")
        updated_at = None if row is None else row.get("updated_at")
        updated_by = None if row is None else row.get("updated_by")
        if cog in self.faults:
            state = "faulted"
        elif enabled:
            state = "online"
        else:
            state = "offline"
        return CogState(
            cog=cog,
            enabled=enabled,
            reason=reason,
            updated_at=updated_at,
            updated_by=updated_by,
            state=state,
        )

    async def cog_state(self, guild_id: int, cog: str) -> CogState:
        if cog not in COGS:
            raise UnknownCogError(cog)
        key = (guild_id, cog)
        now = utcnow().timestamp()
        cached = self._cache.get(key)
        if cached and cached[0] > now:
            return cached[1]
        row = await self.db.fetchone(
            "SELECT enabled, reason, updated_at, updated_by FROM cog_state "
            "WHERE guild_id = ? AND cog = ?",
            (guild_id, cog),
        )
        state = self._state_from_row(cog, row)
        self._cache[key] = (now + CACHE_TTL_S, state)
        return state

    async def cog_states(self, guild_id: int) -> dict[str, CogState]:
        rows = await self.db.fetchall(
            "SELECT cog, enabled, reason, updated_at, updated_by FROM cog_state "
            "WHERE guild_id = ?",
            (guild_id,),
        )
        by_cog = {row["cog"]: row for row in rows}
        return {cog: self._state_from_row(cog, by_cog.get(cog)) for cog in COGS}

    async def is_enabled(self, guild_id: int, cog: str) -> bool:
        state = await self.cog_state(guild_id, cog)
        return state.enabled and cog not in self.faults

    async def set_cog_state(
        self,
        guild_id: int,
        cog: str,
        enabled: bool,
        actor_id: int | None,
        reason: str | None = None,
        source: str = "bot",
        *,
        conn: sqlite3.Connection | None = None,
        request_id: str | None = None,
    ) -> CogState:
        if cog not in COGS:
            raise UnknownCogError(cog)
        if not enabled and cog in LOCKED:
            raise LockedCogError(cog)
        if cog not in TOGGLEABLE and enabled is False:
            raise LockedCogError(cog)

        if conn is None:
            async with self.db.transaction() as transaction:
                return await self.set_cog_state(guild_id, cog, enabled, actor_id, reason, source, conn=transaction, request_id=request_id)

        await self.ensure_guild(guild_id, conn=conn)
        reason = (reason or "").strip() or None
        if reason and len(reason) > 500:
            reason = reason[:500]
        now = iso()
        await self._execute(
            "INSERT INTO cog_state (guild_id, cog, enabled, reason, updated_by, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(guild_id, cog) DO UPDATE SET "
            "enabled = excluded.enabled, reason = excluded.reason, "
            "updated_by = excluded.updated_by, updated_at = excluded.updated_at",
            (guild_id, cog, 1 if enabled else 0, reason, actor_id, now), conn,
        )

        held = 0
        if cog == "dev" and not enabled:
            row = await self._fetchone("SELECT COUNT(*) AS n FROM announcements WHERE guild_id = ? AND status = 'pending'", (guild_id,), conn)
            held = int((row or {}).get("n") or 0)
        detail = {"enabled": enabled, "reason": reason, "source": source, "held_announcements": held}
        if request_id:
            detail["request_id"] = request_id
        audit_id = await self.audit.record(
            guild_id=guild_id,
            actor_id=actor_id,
            action="cog.toggle",
            target=cog,
            detail=detail, conn=conn,
        )
        event = {
                    "type": "cog.toggle",
                    "guild_id": guild_id,
                    "cog": cog,
                    "enabled": enabled,
                    "reason": reason,
                    "source": source,
                    "actor_id": actor_id,
                    "state": "offline" if not enabled else "online",
        }
        if request_id:
            event["request_id"] = request_id
        self.db.after_commit(conn, lambda: self.invalidate(guild_id, cog))
        if self.bus is not None:
            self.db.after_commit(conn, lambda: self.bus.publish(event))
        state = self._state_from_row(cog, {"enabled": enabled, "reason": reason, "updated_at": now, "updated_by": actor_id})
        log.info(
            "cog toggle guild=%s cog=%s enabled=%s source=%s held=%s",
            guild_id,
            cog,
            enabled,
            source,
            held,
        )
        return replace(state, audit_id=audit_id, held_announcements=held)

    # ---------------------------------------------------------- guild config
    async def settings_row(self, guild_id: int, *, use_cache: bool = True, conn: sqlite3.Connection | None = None) -> dict[str, Any]:
        now = utcnow().timestamp()
        cached = self._settings_cache.get(guild_id)
        if conn is None and use_cache and cached and cached[0] > now:
            return cached[1]
        row = await self._fetchone(
            "SELECT announce_channel, github_channel, mod_log_channel, timezone, settings "
            "FROM guild_settings WHERE guild_id = ?",
            (guild_id,), conn,
        )
        if row is None:
            await self.ensure_guild(guild_id, conn=conn)
            row = await self._fetchone(
                "SELECT announce_channel, github_channel, mod_log_channel, timezone, settings FROM guild_settings WHERE guild_id = ?",
                (guild_id,), conn,
            )
        data = row or {
            "announce_channel": None,
            "github_channel": None,
            "mod_log_channel": None,
            "timezone": self.config.default_timezone,
            "settings": "{}",
        }
        try:
            extra = json.loads(data["settings"])
        except (ValueError, TypeError):
            extra = {}
        data["prefix"] = extra.get("prefix") if isinstance(extra, dict) else None
        if conn is None:
            self._settings_cache[guild_id] = (now + CACHE_TTL_S, data)
        return data

    async def get_setting(self, guild_id: int, key: str, *, conn: sqlite3.Connection | None = None) -> Any:
        if key not in CONFIG_KEYS:
            raise KeyError(key)
        row = await self.settings_row(guild_id, conn=conn)
        column = "mod_log_channel" if key == "mod_log" else key
        return row.get(column)

    async def command_prefix(self, guild_id: int) -> str | None:
        try:
            return validate_prefix(await self.get_setting(guild_id, "prefix"))
        except ValueError:
            # An older/manual setting must not enable prefixless invocation.
            log.warning("invalid stored prefix for guild=%s; using mentions only", guild_id)
            return None

    async def timezone(self, guild_id: int) -> ZoneInfo:
        name = await self.get_setting(guild_id, "timezone")
        try:
            return ZoneInfo(str(name))
        except Exception:
            return ZoneInfo(self.config.default_timezone)

    async def update_setting(
        self,
        guild_id: int,
        key: str,
        value: Any,
        actor_id: int | None,
        source: str = "bot",
        *,
        conn: sqlite3.Connection | None = None,
        request_id: str | None = None,
    ) -> Any:
        if key not in CONFIG_KEYS:
            raise KeyError(key)
        if key == "prefix":
            value = validate_prefix(value)
        elif key == "timezone":
            try:
                ZoneInfo(str(value))
            except (KeyError, ValueError, TypeError) as exc:
                raise ValueError("Choose a valid IANA timezone, such as Asia/Manila.") from exc
        elif key in CHANNEL_KEYS and value is not None:
            if not isinstance(value, int) or isinstance(value, bool) or not 0 < value <= 2**63 - 1:
                raise ValueError("Choose a text channel in this server.")
        if conn is None:
            async with self.db.transaction() as transaction:
                return await self.update_setting(guild_id, key, value, actor_id, source, conn=transaction, request_id=request_id)
        column = "mod_log_channel" if key == "mod_log" else key
        before = await self.get_setting(guild_id, key, conn=conn)
        await self.ensure_guild(guild_id, conn=conn)
        if key == "prefix":
            # Update one JSON key atomically, preserving existing miscellaneous
            # settings and avoiding a schema migration for deployed databases.
            await self._execute(
                "UPDATE guild_settings SET settings = json_set("
                "CASE WHEN json_valid(settings) THEN CASE WHEN json_type(settings) = 'object' THEN settings ELSE '{}' END ELSE '{}' END, '$.prefix', ?), "
                "updated_at = ? WHERE guild_id = ?",
                (value, iso(), guild_id), conn,
            )
        else:
            await self._execute(
                f"UPDATE guild_settings SET {column} = ?, updated_at = ? WHERE guild_id = ?",
                (value, iso(), guild_id), conn,
            )
        detail = {"before": before, "after": value, "source": source}
        if request_id:
            detail["request_id"] = request_id
        await self.audit.record(
            guild_id=guild_id,
            actor_id=actor_id,
            action="config.set",
            target=key,
            detail=detail, conn=conn,
        )
        event = {
                    "type": "config.set",
                    "guild_id": guild_id,
                    "key": key,
                    "value": value,
                    "before": before,
                    "source": source,
                    "actor_id": actor_id,
        }
        if request_id:
            event["request_id"] = request_id
        self.db.after_commit(conn, lambda: self.invalidate(guild_id))
        if self.bus is not None:
            self.db.after_commit(conn, lambda: self.bus.publish(event))
        return value

    async def set_setting(self, guild_id: int, key: str, value: Any, actor_id: int | None, source: str = "bot", **kwargs: Any) -> Any:
        """Shared web/Discord validation; preserve the existing Discord entry point."""
        return await self.update_setting(guild_id, key, value, actor_id, source, **kwargs)

    # ------------------------------------------------------------- defaults
    async def seed_defaults(self) -> None:
        """Backfill guild_settings rows for guilds created by older builds."""
        rows = await self.db.fetchall(
            "SELECT g.id FROM guilds g LEFT JOIN guild_settings s ON s.guild_id = g.id "
            "WHERE s.guild_id IS NULL"
        )
        for row in rows:
            await self.ensure_guild(row["id"])

    def describe_lock(self, cog: str) -> str:
        return (
            f"`{cog}` cannot be turned off — it is one of the core cogs "
            f"(`{'`, `'.join(sorted(LOCKED))}`) that keep the bot recoverable."
        )
