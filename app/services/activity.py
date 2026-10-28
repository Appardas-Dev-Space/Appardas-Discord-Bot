"""Owner-only, append-only accountable events; never a page-view tracker."""

from __future__ import annotations

import ipaddress
import json
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone
from typing import Any, Mapping

from ..db import Database
from ..util import iso, utcnow
from .accounts import Account

SECRET_KEYS = frozenset({"password", "token", "secret", "cookie", "authorization", "code", "csrf", "session"})
ACTIVITY_ACTIONS = (
    "auth.login.success", "auth.login.denied_unknown", "auth.login.denied_suspended", "auth.login.denied_revoked",
    "auth.login.state_mismatch", "auth.login.error", "auth.logout", "auth.session.expired", "authz.denied",
    "account.create", "account.update", "account.credentials.reset", "account.password_change", "account.suspend", "account.reactivate", "account.revoke",
    "account.guild.grant", "account.guild.revoke", "cog.toggle", "config.set",
)


def redact(value: Any, depth: int = 0) -> Any:
    if depth > 12:
        return {"truncated": True}
    if isinstance(value, Mapping):
        return {
            str(key)[:100]: redact(item, depth + 1) for key, item in value.items()
            if not SECRET_KEYS.intersection(re.findall(r"[a-z]+", re.sub(r"([a-z])([A-Z])", r"\1_\2", str(key)).lower()))
        }
    if isinstance(value, (list, tuple, set)):
        return [redact(item, depth + 1) for item in list(value)[:100]]
    if isinstance(value, str):
        return value[:500]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:500]


def safe_json(value: Any) -> str | None:
    if value is None:
        return None
    text = json.dumps(redact(value), ensure_ascii=True, separators=(",", ":"), allow_nan=False)
    size = len(text.encode("utf-8"))
    return json.dumps({"truncated": True, "bytes": size}, separators=(",", ":")) if size > 4096 else text


def remote_ip(request: Any, trust_proxy: bool = False) -> str | None:
    value = request.remote
    if trust_proxy:
        value = request.headers.get("X-Forwarded-For", "").split(",", 1)[0].strip() or value
    try:
        return str(ipaddress.ip_address(value)) if value else None
    except ValueError:
        return None


class ActivityService:
    def __init__(self, db: Database, *, trust_proxy: bool = False) -> None:
        self.db = db
        self.trust_proxy = trust_proxy

    async def record(
        self, *, request: Any, action: str, outcome: str = "ok", account: Account | None = None,
        guild_id: int | None = None, target: str | None = None, before: dict | None = None,
        after: dict | None = None, detail: dict | None = None, conn: sqlite3.Connection | None = None,
    ) -> int:
        account = account or request.get("account")
        identity = account.discord_user_id if account else request.get("discord_user_id")
        params = (
            request["request_id"], account.id if account else None, identity,
            account.name if account else None, account.role if account else None, action, outcome,
            guild_id, str(target)[:200] if target is not None else None,
            safe_json(before), safe_json(after), remote_ip(request, self.trust_proxy),
            request.headers.get("User-Agent", "")[:200], safe_json(detail), iso(),
        )
        sql = "INSERT INTO activity_log (request_id, account_id, discord_user_id, account_label, role, action, outcome, guild_id, target, before_json, after_json, ip, user_agent, detail, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        cursor = await self.db.execute_on(conn, sql, params) if conn is not None else await self.db.execute(sql, params)
        return int(cursor.lastrowid)

    @staticmethod
    def filters(query: Mapping[str, str]) -> dict[str, str]:
        result = {}
        for key in ("account", "action", "outcome", "guild", "from", "to", "before", "request_id"):
            value = str(query.get(key, ""))[:200]
            if not value:
                continue
            if key in ("account", "guild", "before") and value not in ({"anonymous"} if key == "account" else {"global"} if key == "guild" else set()):
                if not value.isascii() or not value.isdigit() or not 0 < int(value) <= 2**63 - 1:
                    raise ValueError(f"Invalid {key} filter")
            if key == "outcome" and value not in ("ok", "denied", "error"):
                raise ValueError("Invalid outcome filter")
            if key == "request_id" and not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value):
                raise ValueError("Invalid request filter")
            if key in ("from", "to"):
                date.fromisoformat(value)
            result[key] = value
        if result.get("from") and result.get("to") and result["from"] > result["to"]:
            raise ValueError("From must not be after To")
        result["limit"] = query.get("limit", "50") if query.get("limit", "50") in ("50", "100", "200") else "50"
        return result

    @staticmethod
    def _where(filters: Mapping[str, str]) -> tuple[str, list[Any]]:
        clauses, params = [], []
        for key, column, anonymous in (("account", "account_id", "anonymous"), ("guild", "guild_id", "global")):
            value = filters.get(key)
            if value == anonymous:
                clauses.append(f"{column} IS NULL")
            elif value:
                clauses.append(f"{column} = ?")
                params.append(int(value))
        for key in ("action", "outcome"):
            if filters.get(key):
                clauses.append(f"{key} = ?")
                params.append(filters[key])
        if filters.get("request_id"):
            clauses.append("request_id = ?")
            params.append(filters["request_id"])
        for key, operator in (("from", ">="), ("to", "<")):
            if filters.get(key):
                day = datetime.combine(date.fromisoformat(filters[key]), datetime.min.time(), timezone.utc)
                if key == "to":
                    day += timedelta(days=1)
                clauses.append(f"created_at {operator} ?")
                params.append(iso(day))
        if filters.get("before"):
            clauses.append("id < ?")
            params.append(int(filters["before"]))
        return (" WHERE " + " AND ".join(clauses) if clauses else ""), params

    async def query(self, filters: Mapping[str, str], *, limit: int | None = None) -> list[dict[str, Any]]:
        where, params = self._where(filters)
        return await self.db.fetchall("SELECT * FROM activity_log" + where + " ORDER BY id DESC LIMIT ?", [*params, min(500, limit or int(filters.get("limit", "50")))])

    async def count(self, filters: Mapping[str, str]) -> int:
        where, params = self._where(filters)
        return int(await self.db.scalar("SELECT COUNT(*) FROM activity_log" + where, params) or 0)

    async def prune(self, days: int) -> int:
        if not 30 <= days <= 3650:
            raise ValueError("Activity retention must be 30–3650 days")
        cursor = await self.db.execute("DELETE FROM activity_log WHERE created_at < ?", (iso(utcnow() - timedelta(days=days)),))
        return cursor.rowcount


async def record_activity(*, request: Any, **kwargs: Any) -> int:
    """Every HTTP event reads the same request context and request ID."""
    return await request["activity_service"].record(request=request, **kwargs)
