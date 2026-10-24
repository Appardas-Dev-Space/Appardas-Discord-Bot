"""Command usage logging: track executed commands, user, channel, args, outcome."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import timedelta
from typing import Any

from ..db import Database
from ..util import iso, utcnow

log = logging.getLogger(__name__)


class CommandLogService:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def record(
        self,
        *,
        guild_id: int | None,
        channel_id: int | None,
        user_id: int,
        user_name: str,
        command: str,
        options: dict[str, Any] | str | None = None,
        status: str = "success",
        conn: sqlite3.Connection | None = None,
    ) -> int:
        options_json = (
            options
            if isinstance(options, str)
            else json.dumps(options or {}, separators=(",", ":"), default=str)
        )
        sql = (
            "INSERT INTO command_log "
            "(guild_id, channel_id, user_id, user_name, command, options, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
        )
        params = (
            guild_id,
            channel_id,
            user_id,
            user_name,
            command,
            options_json,
            status,
            iso(),
        )
        cursor = (
            await self.db.execute_on(conn, sql, params)
            if conn is not None
            else await self.db.execute(sql, params)
        )
        return int(cursor.lastrowid or 0)

    async def set_status(
        self,
        log_id: int,
        status: str,
        *,
        conn: sqlite3.Connection | None = None,
    ) -> bool:
        sql = "UPDATE command_log SET status = ? WHERE id = ?"
        params = (status, log_id)
        cursor = (
            await self.db.execute_on(conn, sql, params)
            if conn is not None
            else await self.db.execute(sql, params)
        )
        return (cursor.rowcount or 0) > 0

    async def tail(
        self,
        guild_id: int | None,
        limit: int = 50,
        *,
        command: str = "",
        user_id: int | None = None,
        status: str = "",
        before: int | None = None,
    ) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 200))
        clauses, params = [], []
        if guild_id is not None:
            clauses.append("guild_id = ?")
            params.append(guild_id)
        if command:
            clauses.append("command = ?")
            params.append(command[:100])
        if user_id is not None:
            clauses.append("user_id = ?")
            params.append(user_id)
        if status:
            clauses.append("status = ?")
            params.append(status[:20])
        if before is not None:
            clauses.append("id < ?")
            params.append(before)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        return await self.db.fetchall(
            "SELECT id, guild_id, channel_id, user_id, user_name, command, options, status, created_at "
            "FROM command_log" + where + " ORDER BY id DESC LIMIT ?",
            [*params, limit],
        )

    async def distinct_commands(self, guild_id: int | None = None) -> list[str]:
        if guild_id is not None:
            rows = await self.db.fetchall(
                "SELECT DISTINCT command FROM command_log WHERE guild_id = ? ORDER BY command",
                (guild_id,),
            )
        else:
            rows = await self.db.fetchall(
                "SELECT DISTINCT command FROM command_log ORDER BY command"
            )
        return [row["command"] for row in rows]

    async def prune(self, days: int) -> int:
        cutoff = iso(utcnow() - timedelta(days=max(1, days)))
        cursor = await self.db.execute(
            "DELETE FROM command_log WHERE created_at < ?", (cutoff,)
        )
        return int(cursor.rowcount or 0)
