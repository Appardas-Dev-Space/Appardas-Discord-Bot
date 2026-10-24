"""Closed-registration accounts: Discord proves identity or operator credentials grant access."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import sqlite3
import unicodedata
from dataclasses import dataclass, fields
from typing import Any, Iterable

from ..db import Database
from ..util import iso


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    iterations = 100_000
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations)
    return f"pbkdf2_sha256${iterations}${salt}${dk.hex()}"


def verify_password(password: str, stored_hash: str | None) -> bool:
    if not stored_hash or not stored_hash.startswith("pbkdf2_sha256$"):
        return False
    try:
        parts = stored_hash.split("$")
        if len(parts) != 4:
            return False
        _, iter_str, salt, expected_hex = parts
        iterations = int(iter_str)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), iterations)
        return hmac.compare_digest(dk.hex(), expected_hex)
    except Exception:
        return False


class AccountError(ValueError):
    def __init__(self, message: str, field: str = "account", existing_id: int | None = None) -> None:
        super().__init__(message)
        self.field = field
        self.existing_id = existing_id


@dataclass(frozen=True, slots=True)
class Account:
    id: int
    discord_user_id: int | None
    name: str
    role: str
    status: str
    is_system: bool
    note: str | None
    created_by: int | None
    created_at: str
    updated_at: str
    username: str | None = None
    must_change_password: bool = False
    last_login_at: str | None = None
    last_login_ip: str | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> Account:
        data = {field.name: row[field.name] for field in fields(cls) if field.name in row}
        data["is_system"] = bool(data.get("is_system", 0))
        data["must_change_password"] = bool(data.get("must_change_password", 0))
        for field in fields(cls):
            if field.name not in data:
                data[field.name] = field.default if field.default is not None else None
        return cls(**data)

    @property
    def can_write(self) -> bool:
        return self.status == "active" and self.role in ("admin", "owner")


def validate_discord_id(value: object) -> int:
    text = str(value)
    if not re.fullmatch(r"[1-9][0-9]{16,19}", text) or int(text) > 2**63 - 1:
        raise AccountError("That does not look like a Discord user ID. It is a 17–20 digit number.", "discord_user_id")
    return int(text)


def validate_identity(name: str, note: str | None) -> tuple[str, str | None]:
    if not 1 <= len(name) <= 64 or not name.strip() or any(unicodedata.category(c).startswith("C") for c in name):
        raise AccountError("Display name must be 1–64 visible characters.", "name")
    note = note or None
    if note is not None and (len(note) > 280 or any(unicodedata.category(c).startswith("C") and c not in "\n\t" for c in note)):
        raise AccountError("Note must be at most 280 characters.", "note")
    return name.strip(), note


def bootstrap_owners(conn: sqlite3.Connection, owner_ids: Iterable[int]) -> None:
    """Called inside the schema migration transaction, before accepting requests."""
    owners = frozenset(owner_ids)
    if not owners or any(not 0 < value <= 2**63 - 1 for value in owners):
        raise AccountError("BOT_OWNER_IDS must contain at least one valid ID")
    now = iso()
    removed = conn.execute(
        "SELECT id, discord_user_id FROM accounts WHERE (role = 'owner' OR is_system = 1) AND discord_user_id IS NOT NULL"
    ).fetchall()
    for row in removed:
        if row["discord_user_id"] not in owners:
            conn.execute("UPDATE accounts SET role = 'viewer', is_system = 0, updated_at = ? WHERE id = ?", (now, row["id"]))
            conn.execute("DELETE FROM sessions WHERE account_id = ?", (row["id"],))
    for discord_id in sorted(owners):
        conn.execute(
            "INSERT INTO accounts (discord_user_id, name, role, status, is_system, created_at, updated_at) "
            "VALUES (?, ?, 'owner', 'active', 1, ?, ?) ON CONFLICT(discord_user_id) DO UPDATE SET "
            "role = 'owner', status = 'active', is_system = 1, created_by = NULL, updated_at = excluded.updated_at",
            (discord_id, f"Owner {discord_id}", now, now),
        )


def bootstrap_preliminary_admin(conn: sqlite3.Connection) -> None:
    """Seed preliminary admin account (appardas-admin / admin) with mandatory password reset."""
    now = iso()
    existing = conn.execute("SELECT id, username FROM accounts WHERE username = 'appardas-admin'").fetchone()
    if not existing:
        pwd_hash = hash_password("admin")
        cursor = conn.execute(
            "INSERT INTO accounts (username, password_hash, must_change_password, name, role, status, is_system, note, created_at, updated_at) "
            "VALUES (?, ?, 1, 'Appardas Admin', 'owner', 'active', 1, 'Preliminary system administrator', ?, ?)",
            ("appardas-admin", pwd_hash, now, now),
        )
        admin_id = cursor.lastrowid
        guilds = conn.execute("SELECT id FROM guilds").fetchall()
        for g in guilds:
            conn.execute(
                "INSERT OR IGNORE INTO account_guilds (account_id, guild_id, granted_at) VALUES (?, ?, ?)",
                (admin_id, g["id"], now),
            )


class AccountsService:
    def __init__(self, db: Database) -> None:
        self.db = db

    async def get(self, account_id: int, *, conn: sqlite3.Connection | None = None) -> Account | None:
        sql = "SELECT * FROM accounts WHERE id = ?"
        row = await self.db.fetchone_on(conn, sql, (account_id,)) if conn is not None else await self.db.fetchone(sql, (account_id,))
        return Account.from_row(row) if row else None

    async def by_discord_id(self, discord_id: int, *, conn: sqlite3.Connection | None = None) -> Account | None:
        sql = "SELECT * FROM accounts WHERE discord_user_id = ?"
        row = await self.db.fetchone_on(conn, sql, (discord_id,)) if conn is not None else await self.db.fetchone(sql, (discord_id,))
        return Account.from_row(row) if row else None

    async def by_username(self, username: str, *, conn: sqlite3.Connection | None = None) -> Account | None:
        sql = "SELECT * FROM accounts WHERE lower(username) = lower(?)"
        row = await self.db.fetchone_on(conn, sql, (username.strip(),)) if conn is not None else await self.db.fetchone(sql, (username.strip(),))
        return Account.from_row(row) if row else None

    async def authenticate(self, username: str, password: str, *, ip: str | None = None, conn: sqlite3.Connection | None = None) -> Account | None:
        account = await self.by_username(username, conn=conn)
        if not account or account.status != "active":
            return None
        sql = "SELECT password_hash FROM accounts WHERE id = ?"
        row = await self.db.fetchone_on(conn, sql, (account.id,)) if conn is not None else await self.db.fetchone(sql, (account.id,))
        if not row or not row.get("password_hash"):
            return None
        if not verify_password(password, row["password_hash"]):
            return None
        now = iso()
        update_sql = "UPDATE accounts SET last_login_at = ?, last_login_ip = ?, updated_at = ? WHERE id = ?"
        if conn is not None:
            await self.db.execute_on(conn, update_sql, (now, ip, now, account.id))
        else:
            await self.db.execute(update_sql, (now, ip, now, account.id))
        return await self.get(account.id, conn=conn)

    async def change_password(self, account_id: int, old_password: str, new_password: str, *, conn: sqlite3.Connection | None = None) -> Account:
        if conn is None:
            async with self.db.transaction() as transaction:
                return await self.change_password(account_id, old_password, new_password, conn=transaction)
        account = await self.get(account_id, conn=conn)
        if not account:
            raise AccountError("Account not found")
        row = await self.db.fetchone_on(conn, "SELECT password_hash FROM accounts WHERE id = ?", (account_id,))
        if not row or not row.get("password_hash") or not verify_password(old_password, row["password_hash"]):
            raise AccountError("Current password does not match.", "current_password")
        if new_password == "admin":
            raise AccountError("New password cannot be the default password.", "new_password")
        if len(new_password) < 6:
            raise AccountError("New password must be at least 6 characters.", "new_password")
        if new_password == old_password:
            raise AccountError("New password must be different from current password.", "new_password")
        new_hash = hash_password(new_password)
        now = iso()
        await self.db.execute_on(conn, "UPDATE accounts SET password_hash = ?, must_change_password = 0, updated_at = ? WHERE id = ?", (new_hash, now, account_id))
        res = await self.get(account_id, conn=conn)
        assert res is not None
        return res

    async def _owner(self, actor: Account, conn: sqlite3.Connection) -> None:
        current = await self.get(actor.id, conn=conn)
        if current is None or current.status != "active" or current.role != "owner":
            raise PermissionError("Only the owner can manage accounts")

    async def list(self, *, q: str = "", role: str = "", status: str = "") -> list[dict[str, Any]]:
        clauses, params = [], []
        if q:
            clauses.append("(instr(lower(a.name), lower(?)) > 0 OR instr(CAST(a.discord_user_id AS TEXT), ?) > 0)")
            params.extend((q[:200], q[:200]))
        if role in ("owner", "admin", "viewer"):
            clauses.append("a.role = ?")
            params.append(role)
        if status in ("active", "suspended", "revoked"):
            clauses.append("a.status = ?")
            params.append(status)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        return await self.db.fetchall(
            "SELECT a.*, creator.name AS creator_name, "
            "(SELECT COUNT(*) FROM account_guilds ag WHERE ag.account_id = a.id) AS guild_count "
            "FROM accounts a LEFT JOIN accounts creator ON creator.id = a.created_by" + where +
            " ORDER BY CASE a.status WHEN 'active' THEN 0 WHEN 'suspended' THEN 1 ELSE 2 END, "
            "CASE a.role WHEN 'owner' THEN 0 WHEN 'admin' THEN 1 ELSE 2 END, lower(a.name), a.id",
            params,
        )

    async def guilds(self, account_id: int, *, conn: sqlite3.Connection | None = None) -> list[dict[str, Any]]:
        sql = (
            "SELECT g.*, ag.granted_at, ag.granted_by FROM account_guilds ag JOIN guilds g ON g.id = ag.guild_id "
            "WHERE ag.account_id = ? ORDER BY lower(g.name), g.id"
        )
        return await self.db.fetchall_on(conn, sql, (account_id,)) if conn is not None else await self.db.fetchall(sql, (account_id,))

    async def has_grant(self, account: Account, guild_id: int, *, conn: sqlite3.Connection | None = None) -> bool:
        if account.status != "active":
            return False
        if account.role == "owner":
            return True
        sql = "SELECT 1 AS ok FROM account_guilds WHERE account_id = ? AND guild_id = ?"
        params = (account.id, guild_id)
        row = await self.db.fetchone_on(conn, sql, params) if conn is not None else await self.db.fetchone(sql, params)
        return row is not None

    async def create(
        self, *, actor: Account, discord_user_id: object, name: str, role: str,
        guild_ids: Iterable[int] = (), note: str | None = None, conn: sqlite3.Connection | None = None,
    ) -> Account:
        if conn is None:
            async with self.db.transaction() as transaction:
                return await self.create(actor=actor, discord_user_id=discord_user_id, name=name, role=role, guild_ids=guild_ids, note=note, conn=transaction)
        await self._owner(actor, conn)
        discord_id = validate_discord_id(discord_user_id)
        name, note = validate_identity(name, note)
        if role not in ("admin", "viewer"):
            raise AccountError("Choose admin or viewer. Owners are managed by BOT_OWNER_IDS.", "role")
        duplicate = await self.by_discord_id(discord_id, conn=conn)
        if duplicate:
            raise AccountError("That Discord ID already has an account. Open it to change the role or reactivate it.", "discord_user_id", duplicate.id)
        grants = set(guild_ids)
        await self._validate_grants(grants, conn)
        now = iso()
        try:
            cursor = await self.db.execute_on(conn,
                "INSERT INTO accounts (discord_user_id, name, role, note, created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (discord_id, name, role, note, actor.id, now, now),
            )
        except sqlite3.IntegrityError as exc:
            raise AccountError("That Discord ID already has an account.", "discord_user_id") from exc
        account_id = int(cursor.lastrowid)
        for guild_id in sorted(grants):
            await self.db.execute_on(conn, "INSERT INTO account_guilds (account_id, guild_id, granted_by, granted_at) VALUES (?, ?, ?, ?)", (account_id, guild_id, actor.id, now))
        account = await self.get(account_id, conn=conn)
        assert account is not None
        return account

    async def update(self, account_id: int, *, actor: Account, name: str, note: str | None, role: str | None = None, conn: sqlite3.Connection | None = None) -> Account:
        if conn is None:
            async with self.db.transaction() as transaction:
                return await self.update(account_id, actor=actor, name=name, note=note, role=role, conn=transaction)
        await self._owner(actor, conn)
        target = await self.get(account_id, conn=conn)
        if target is None:
            raise AccountError("Account not found")
        name, note = validate_identity(name, note)
        if role is not None and (target.is_system or role not in ("admin", "viewer")):
            raise AccountError("Owners are managed by BOT_OWNER_IDS. Choose admin or viewer for other accounts.", "role")
        await self.db.execute_on(conn, "UPDATE accounts SET name = ?, note = ?, role = ?, updated_at = ? WHERE id = ?", (name, note, role or target.role, iso(), account_id))
        result = await self.get(account_id, conn=conn)
        assert result is not None
        return result

    async def set_status(self, account_id: int, status: str, *, actor: Account, conn: sqlite3.Connection | None = None) -> tuple[Account, int]:
        if conn is None:
            async with self.db.transaction() as transaction:
                return await self.set_status(account_id, status, actor=actor, conn=transaction)
        await self._owner(actor, conn)
        target = await self.get(account_id, conn=conn)
        if target is None:
            raise AccountError("Account not found")
        if target.is_system:
            raise AccountError("Managed by BOT_OWNER_IDS. It cannot be suspended or demoted here.")
        if status not in ("active", "suspended", "revoked"):
            raise AccountError("Choose active, suspended, or revoked", "status")
        await self.db.execute_on(conn, "UPDATE accounts SET status = ?, updated_at = ? WHERE id = ?", (status, iso(), account_id))
        deleted = 0
        if status != "active":
            cursor = await self.db.execute_on(conn, "DELETE FROM sessions WHERE account_id = ?", (account_id,))
            deleted = cursor.rowcount
        result = await self.get(account_id, conn=conn)
        assert result is not None
        return result, deleted

    async def _validate_grants(self, guild_ids: set[int], conn: sqlite3.Connection) -> None:
        for guild_id in guild_ids:
            row = await self.db.fetchone_on(conn, "SELECT id FROM guilds WHERE id = ? AND left_at IS NULL", (guild_id,))
            if not row:
                raise AccountError("Only servers appardas is currently in can be granted.", "guilds")

    async def set_grants(self, account_id: int, guild_ids: Iterable[int], *, actor: Account, conn: sqlite3.Connection | None = None) -> tuple[set[int], set[int]]:
        if conn is None:
            async with self.db.transaction() as transaction:
                return await self.set_grants(account_id, guild_ids, actor=actor, conn=transaction)
        await self._owner(actor, conn)
        target = await self.get(account_id, conn=conn)
        if target is None or target.is_system:
            raise AccountError("System owners already have every server. Their grants are not editable.", "guilds")
        current = {row["guild_id"] for row in await self.db.fetchall_on(conn, "SELECT guild_id FROM account_guilds WHERE account_id = ?", (account_id,))}
        selected = set(guild_ids)
        added, removed = selected - current, current - selected
        await self._validate_grants(added, conn)
        for guild_id in sorted(added):
            await self.db.execute_on(conn, "INSERT INTO account_guilds (account_id, guild_id, granted_by, granted_at) VALUES (?, ?, ?, ?)", (account_id, guild_id, actor.id, iso()))
        for guild_id in sorted(removed):
            await self.db.execute_on(conn, "DELETE FROM account_guilds WHERE account_id = ? AND guild_id = ?", (account_id, guild_id))
        return added, removed
