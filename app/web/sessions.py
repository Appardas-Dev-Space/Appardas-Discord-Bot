"""Opaque cookie sessions and one-use OAuth states, hashed at rest in SQLite."""

from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import timedelta
from typing import Any
from urllib.parse import unquote

from aiohttp import web

from ..config import Settings
from ..db import Database
from ..services.accounts import Account
from ..util import iso, parse_iso, utcnow

OAUTH_COOKIE = "appardas_oauth"
OPAQUE_ID = re.compile(r"[A-Za-z0-9_-]{43}\Z")


def id_hash(value: str) -> str:
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def safe_next(value: object) -> str:
    if not isinstance(value, str) or len(value) > 200:
        return "/dashboard"
    decoded = unquote(value)
    if (
        not value.startswith("/") or value.startswith("//") or decoded.startswith("//")
        or "\\" in decoded or "\\" in value
        or any(ord(c) < 32 or ord(c) == 127 for c in decoded)
    ):
        return "/dashboard"
    # Do not send a signed-in browser back into an OAuth loop or a POST-only path.
    if decoded.split("?", 1)[0] in ("/login", "/auth/callback", "/logout"):
        return "/dashboard"
    return value


@dataclass(frozen=True, slots=True)
class Session:
    id_hash: str
    account: Account
    csrf: str
    created_at: str
    last_seen_at: str
    expires_at: str
    last_guild_id: int | None
    username: str | None
    avatar: str | None


class Sessions:
    def __init__(self, db: Database, config: Settings) -> None:
        self.db = db
        self.config = config

    @property
    def cookie_name(self) -> str:
        return "__Host-appardas_session" if self.config.cookie_secure else "appardas_session"

    def set_cookie(self, response: web.StreamResponse, raw_id: str, session: Session | None = None) -> None:
        age = self.config.session_idle_days * 86400
        if session is not None:
            age = max(1, min(age, int((parse_iso(session.expires_at) - utcnow()).total_seconds())))
        response.set_cookie(self.cookie_name, raw_id, max_age=age, httponly=True, secure=self.config.cookie_secure, samesite="Lax", path="/")

    def clear_cookie(self, response: web.StreamResponse) -> None:
        response.del_cookie(self.cookie_name, path="/", secure=self.config.cookie_secure, httponly=True, samesite="Lax")

    def set_oauth_cookie(self, response: web.StreamResponse, state: str) -> None:
        response.set_cookie(OAUTH_COOKIE, state, max_age=600, httponly=True, secure=self.config.cookie_secure, samesite="Lax", path="/auth")

    def clear_oauth_cookie(self, response: web.StreamResponse) -> None:
        response.del_cookie(OAUTH_COOKIE, path="/auth", secure=self.config.cookie_secure, httponly=True, samesite="Lax")

    async def create(self, account: Account, *, username: str | None = None, avatar: str | None = None, conn: sqlite3.Connection | None = None) -> str:
        if conn is None:
            async with self.db.transaction() as transaction:
                return await self.create(account, username=username, avatar=avatar, conn=transaction)
        row = await self.db.fetchone_on(conn, "SELECT status FROM accounts WHERE id = ?", (account.id,))
        if not row or row["status"] != "active":
            raise PermissionError("Account is not active")
        raw_id = secrets.token_urlsafe(32)
        now = utcnow()
        expires = now + timedelta(days=min(self.config.session_idle_days, self.config.session_max_days))
        name = username or account.username or account.name
        await self.db.execute_on(conn,
            "INSERT INTO sessions (id_hash, account_id, discord_user_id, username, avatar, csrf, created_at, last_seen_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (id_hash(raw_id), account.id, account.discord_user_id, (name or "")[:64], (avatar or "")[:100], secrets.token_urlsafe(32), iso(now), iso(now), iso(expires)),
        )
        return raw_id

    async def resolve(self, raw_id: str, *, touch: bool = True, conn: sqlite3.Connection | None = None) -> tuple[Session | None, Account | None, str | None]:
        if not isinstance(raw_id, str) or not OPAQUE_ID.fullmatch(raw_id):
            return None, None, None
        if conn is None:
            # Serialize the join, expiry deletion and throttled touch with logout
            # and suspension. A deleted session must not be resurrected by a touch.
            async with self.db.transaction() as transaction:
                return await self.resolve(raw_id, touch=touch, conn=transaction)
        sql = (
            "SELECT s.*, a.id AS a_id, a.discord_user_id AS a_discord_user_id, a.username AS a_username, "
            "a.must_change_password, a.name, a.role, a.status, a.is_system, a.note, a.created_by, "
            "a.created_at AS a_created_at, a.updated_at, a.last_login_at, a.last_login_ip FROM sessions s JOIN accounts a ON a.id = s.account_id WHERE s.id_hash = ?"
        )
        params = (id_hash(raw_id),)
        row = await self.db.fetchone_on(conn, sql, params) if conn is not None else await self.db.fetchone(sql, params)
        if row is None:
            return None, None, None
        account_row = dict(
            row,
            id=row["a_id"],
            discord_user_id=row["a_discord_user_id"],
            username=row.get("a_username"),
            must_change_password=row.get("must_change_password", 0),
            created_at=row["a_created_at"],
        )
        account = Account.from_row(account_row)
        now = utcnow()
        absolute = parse_iso(row["created_at"]) + timedelta(days=self.config.session_max_days)
        idle = parse_iso(row["last_seen_at"]) + timedelta(days=self.config.session_idle_days)
        reason = None
        if account.status != "active" or account.discord_user_id != row["discord_user_id"]:
            reason = "account_inactive"
        elif now >= absolute:
            reason = "absolute"
        elif now >= idle or now >= parse_iso(row["expires_at"]):
            reason = "idle"
        if reason:
            sql = "DELETE FROM sessions WHERE id_hash = ?"
            if conn is not None:
                await self.db.execute_on(conn, sql, params)
            else:
                await self.db.execute(sql, params)
            return None, account, reason
        if touch and (now - parse_iso(row["last_seen_at"])).total_seconds() >= 60:
            row["last_seen_at"] = iso(now)
            row["expires_at"] = iso(min(absolute, now + timedelta(days=self.config.session_idle_days)))
            sql = "UPDATE sessions SET last_seen_at = ?, expires_at = ? WHERE id_hash = ?"
            values = (row["last_seen_at"], row["expires_at"], row["id_hash"])
            if conn is not None:
                await self.db.execute_on(conn, sql, values)
            else:
                await self.db.execute(sql, values)
        return Session(row["id_hash"], account, row["csrf"], row["created_at"], row["last_seen_at"], row["expires_at"], row["last_guild_id"], row["username"], row["avatar"]), account, None

    async def lookup(self, raw_id: str, *, touch: bool = True, conn: sqlite3.Connection | None = None) -> Session | None:
        return (await self.resolve(raw_id, touch=touch, conn=conn))[0]

    async def delete(self, raw_id: str, *, conn: sqlite3.Connection | None = None) -> None:
        if not OPAQUE_ID.fullmatch(raw_id):
            return
        sql, params = "DELETE FROM sessions WHERE id_hash = ?", (id_hash(raw_id),)
        if conn is not None:
            await self.db.execute_on(conn, sql, params)
        else:
            await self.db.execute(sql, params)

    async def remember_guild(self, session: Session, guild_id: int, *, conn: sqlite3.Connection | None = None) -> None:
        if session.last_guild_id != guild_id:
            sql, params = "UPDATE sessions SET last_guild_id = ? WHERE id_hash = ?", (guild_id, session.id_hash)
            if conn is not None:
                await self.db.execute_on(conn, sql, params)
            else:
                await self.db.execute(sql, params)

    async def create_state(self, next_path: str) -> str:
        state = secrets.token_urlsafe(32)
        now = utcnow()
        await self.db.execute("INSERT INTO oauth_states (state_hash, next_path, created_at, expires_at) VALUES (?, ?, ?, ?)", (id_hash(state), safe_next(next_path), iso(now), iso(now + timedelta(minutes=10))))
        return state

    async def consume_state(self, state: str) -> str | None:
        if not OPAQUE_ID.fullmatch(state):
            return None
        async with self.db.transaction() as conn:
            row = await self.db.fetchone_on(conn, "SELECT next_path, expires_at FROM oauth_states WHERE state_hash = ?", (id_hash(state),))
            await self.db.execute_on(conn, "DELETE FROM oauth_states WHERE state_hash = ?", (id_hash(state),))
            if row and parse_iso(row["expires_at"]) > utcnow():
                return safe_next(row["next_path"])
        return None

    async def prune(self) -> int:
        async with self.db.transaction() as conn:
            now = utcnow()
            cursor = await self.db.execute_on(conn, "DELETE FROM sessions WHERE expires_at <= ? OR created_at <= ? OR last_seen_at <= ?", (iso(now), iso(now - timedelta(days=self.config.session_max_days)), iso(now - timedelta(days=self.config.session_idle_days))))
            await self.db.execute_on(conn, "DELETE FROM oauth_states WHERE expires_at <= ?", (iso(now),))
            return cursor.rowcount
