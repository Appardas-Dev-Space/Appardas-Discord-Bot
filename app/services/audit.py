"""Append-only audit trail: every mutation, its actor and its reason."""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any

from ..db import Database
from ..util import iso, utcnow

log = logging.getLogger(__name__)


class AuditService:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def record(
        self,
        *,
        guild_id: int | None,
        actor_id: int | None,
        action: str,
        target: str | None = None,
        detail: dict[str, Any] | None = None,
        conn: sqlite3.Connection | None = None,
    ) -> int:
        sql = "INSERT INTO audit_log (guild_id, actor_id, action, target, detail, created_at) VALUES (?, ?, ?, ?, ?, ?)"
        params = (
                guild_id,
                actor_id,
                action,
                target,
                json.dumps(detail or {}, separators=(",", ":"), default=str),
                iso(),
        )
        cursor = await self.db.execute_on(conn, sql, params) if conn is not None else await self.db.execute(sql, params)
        return int(cursor.lastrowid or 0)

    async def tail(self, guild_id: int | None, limit: int = 20, *, action: str = "", before: int | None = None) -> list[dict[str, Any]]:
        limit = max(1, min(int(limit), 200))
        clauses, params = [], []
        if guild_id is not None:
            clauses.append("guild_id = ?")
            params.append(guild_id)
        if action:
            clauses.append("action = ?")
            params.append(action[:200])
        if before is not None:
            clauses.append("id < ?")
            params.append(before)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        return await self.db.fetchall(
            "SELECT id, guild_id, actor_id, action, target, detail, created_at "
            "FROM audit_log" + where + " ORDER BY id DESC LIMIT ?", [*params, limit],
        )

    async def prune(self, days: int) -> int:
        from datetime import timedelta

        cutoff = iso(utcnow() - timedelta(days=max(1, days)))
        cursor = await self.db.execute(
            "DELETE FROM audit_log WHERE created_at < ?", (cutoff,)
        )
        return int(cursor.rowcount or 0)
