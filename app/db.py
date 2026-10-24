"""The only module that touches SQLite.

One connection, one process, one writer. Every call is blocking `sqlite3`
work executed off the event loop through `asyncio.to_thread`, serialised by
one lock so a slow query can never stall the gateway heartbeat.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Iterable, Sequence

from .util import iso

log = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

PRAGMAS = (
    "PRAGMA journal_mode = WAL",
    "PRAGMA synchronous = NORMAL",
    "PRAGMA foreign_keys = ON",
    "PRAGMA busy_timeout = 5000",
    "PRAGMA cache_size = -2000",
    "PRAGMA temp_store = MEMORY",
)


class DatabaseError(RuntimeError):
    """Startup-level database problem (corrupt file, unwritable directory)."""


def _dict_factory(cursor: sqlite3.Cursor, row: Sequence[Any]) -> dict[str, Any]:
    return {column[0]: value for column, value in zip(cursor.description, row)}


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._conn: sqlite3.Connection | None = None
        self._lock = asyncio.Lock()
        self.last_write_at: str | None = None
        self._transaction_task: asyncio.Task[Any] | None = None
        self._commit_callbacks: list[Callable[[], None]] = []

    # ------------------------------------------------------------------ setup
    async def connect(self, owner_ids: Iterable[int] | None = None) -> None:
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._conn = await asyncio.to_thread(self._open)
        try:
            os.chmod(self.path, 0o600)
        except OSError:
            log.warning("could not chmod %s to 0600", self.path)
        try:
            await self._verify_integrity()
            async with self._lock:
                await self._thread(self._migrate, None if owner_ids is None else tuple(owner_ids))
        except BaseException:
            await self.close()
            raise

    def _migrate(self, owner_ids: tuple[int, ...] | None) -> None:
        from .services.accounts import bootstrap_owners, bootstrap_preliminary_admin

        meta_exists = self.conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'meta'").fetchone()
        if meta_exists:
            version = self.conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
            if version and version["value"] not in ("1", "2", "3"):
                raise DatabaseError("unsupported database schema; restore compatible code and database together")
        sessions_info = {row["name"]: row for row in self.conn.execute("PRAGMA table_info(sessions)").fetchall()}
        legacy = "DROP TABLE sessions;\n" if sessions_info and ("account_id" not in sessions_info or sessions_info.get("discord_user_id", {}).get("notnull") == 1) else ""
        accounts_info = {row["name"]: row for row in self.conn.execute("PRAGMA table_info(accounts)").fetchall()}
        if accounts_info:
            if accounts_info.get("discord_user_id", {}).get("notnull") == 1:
                self.conn.execute("PRAGMA foreign_keys = OFF")
                if "username" not in accounts_info:
                    self.conn.execute("ALTER TABLE accounts ADD COLUMN username TEXT")
                if "password_hash" not in accounts_info:
                    self.conn.execute("ALTER TABLE accounts ADD COLUMN password_hash TEXT")
                if "must_change_password" not in accounts_info:
                    self.conn.execute("ALTER TABLE accounts ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0")
                self.conn.executescript("""
                    CREATE TABLE accounts_v3_new (
                      id              INTEGER PRIMARY KEY AUTOINCREMENT,
                      discord_user_id INTEGER UNIQUE,
                      username        TEXT UNIQUE,
                      password_hash   TEXT,
                      must_change_password INTEGER NOT NULL DEFAULT 0,
                      name            TEXT NOT NULL,
                      role            TEXT NOT NULL DEFAULT 'viewer' CHECK (role IN ('owner', 'admin', 'viewer')),
                      status          TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended', 'revoked')),
                      is_system       INTEGER NOT NULL DEFAULT 0,
                      note            TEXT,
                      created_by      INTEGER REFERENCES accounts(id) ON DELETE SET NULL,
                      created_at      TEXT NOT NULL,
                      updated_at      TEXT NOT NULL,
                      last_login_at   TEXT,
                      last_login_ip   TEXT
                    );
                    INSERT INTO accounts_v3_new SELECT id, discord_user_id, username, password_hash, must_change_password, name, role, status, is_system, note, created_by, created_at, updated_at, last_login_at, last_login_ip FROM accounts;
                    DROP TABLE accounts;
                    ALTER TABLE accounts_v3_new RENAME TO accounts;
                    CREATE INDEX IF NOT EXISTS idx_accounts_status ON accounts (status);
                    CREATE INDEX IF NOT EXISTS idx_accounts_discord ON accounts (discord_user_id);
                    CREATE INDEX IF NOT EXISTS idx_accounts_username ON accounts (username);
                """)
                self.conn.execute("PRAGMA foreign_keys = ON")
            else:
                account_cols = set(accounts_info.keys())
                if "username" not in account_cols:
                    self.conn.execute("ALTER TABLE accounts ADD COLUMN username TEXT")
                if "password_hash" not in account_cols:
                    self.conn.execute("ALTER TABLE accounts ADD COLUMN password_hash TEXT")
                if "must_change_password" not in account_cols:
                    self.conn.execute("ALTER TABLE accounts ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0")

        try:
            self.conn.executescript("BEGIN IMMEDIATE;\n" + legacy + SCHEMA_PATH.read_text(encoding="utf-8"))
            self.conn.execute("DELETE FROM meta WHERE key LIKE 'dash.jti.%'")
            if owner_ids is not None:
                bootstrap_owners(self.conn, owner_ids)
            bootstrap_preliminary_admin(self.conn)
            if self.conn.execute("PRAGMA foreign_key_check").fetchone():
                raise DatabaseError("foreign-key integrity check failed")
            self.conn.commit()
            self.last_write_at = iso()
        except BaseException as exc:
            self.conn.rollback()
            raise DatabaseError("schema v3 migration / owner bootstrap failed") from exc

    def _open(self) -> sqlite3.Connection:
        try:
            conn = sqlite3.connect(str(self.path), check_same_thread=False, timeout=5.0)
        except sqlite3.Error as exc:  # pragma: no cover - environment dependent
            raise DatabaseError(f"cannot open {self.path}: {exc}") from exc
        conn.row_factory = _dict_factory
        for pragma in PRAGMAS:
            conn.execute(pragma)
        return conn

    async def _verify_integrity(self) -> None:
        row = await self.fetchone("PRAGMA integrity_check")
        result = (row or {}).get("integrity_check")
        if result != "ok":
            raise DatabaseError(
                f"SQLite integrity check failed for {self.path}: {result}. "
                "Restore the latest snapshot from data/backups before starting."
            )

    # ------------------------------------------------------------------ access
    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise DatabaseError("database is not connected")
        return self._conn

    @staticmethod
    async def _thread(func: Callable[..., Any], *args: Any) -> Any:
        """Do not release the writer lock while a cancelled worker is still running."""
        task = asyncio.create_task(asyncio.to_thread(func, *args))
        cancelled = False
        while True:
            try:
                result = await asyncio.shield(task)
                break
            except asyncio.CancelledError:
                cancelled = True
        if cancelled:
            raise asyncio.CancelledError
        return result

    def _check_not_nested(self) -> None:
        if self._transaction_task is asyncio.current_task():
            raise RuntimeError("Inside a transaction, use its supplied connection, not db.execute/fetch")

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[sqlite3.Connection]:
        """One atomic write, including its audit/activity rows; cancellation rolls back.

        Pass the connection explicitly to every service inside this scope. Cache
        invalidation and events are registered with `after_commit`, never published
        before the activity insert and commit succeed.
        """
        self._check_not_nested()
        async with self._lock:
            self._transaction_task = asyncio.current_task()
            self._commit_callbacks = []
            committed = False
            changes_before = self.conn.total_changes
            try:
                await self._thread(self.conn.execute, "BEGIN IMMEDIATE")
                yield self.conn
                # COMMIT is the point of no return. Drain it even on cancellation,
                # then run notifications if it committed; never leave stale caches.
                worker = asyncio.create_task(asyncio.to_thread(self.conn.commit))
                cancelled = False
                while True:
                    try:
                        await asyncio.shield(worker)
                        break
                    except asyncio.CancelledError:
                        cancelled = True
                committed = True
                if self.conn.total_changes != changes_before:
                    self.last_write_at = iso()
                for callback in self._commit_callbacks:
                    try:
                        callback()
                    except Exception:
                        log.exception("post-commit notification failed (write is committed)")
                if cancelled:
                    raise asyncio.CancelledError
            except BaseException:
                if not committed:
                    await self._thread(self.conn.rollback)
                raise
            finally:
                self._commit_callbacks = []
                self._transaction_task = None

    def after_commit(self, conn: sqlite3.Connection, callback: Callable[[], None]) -> None:
        if conn is not self.conn or self._transaction_task is not asyncio.current_task():
            raise RuntimeError("after_commit requires the current transaction connection")
        self._commit_callbacks.append(callback)

    async def execute_on(self, conn: sqlite3.Connection, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        """Transaction-scoped execution, with no lock acquisition or implicit commit."""
        if conn is not self.conn or self._transaction_task is not asyncio.current_task():
            raise RuntimeError("execute_on requires the current transaction connection")
        return await self._thread(conn.execute, sql, tuple(params))

    async def fetchone_on(self, conn: sqlite3.Connection, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
        cursor = await self.execute_on(conn, sql, params)
        return await self._thread(cursor.fetchone)

    async def fetchall_on(self, conn: sqlite3.Connection, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        cursor = await self.execute_on(conn, sql, params)
        return await self._thread(cursor.fetchall)

    async def executescript(self, script: str) -> None:
        self._check_not_nested()
        async with self._lock:
            await self._thread(self._exec_script, script)

    def _exec_script(self, script: str) -> None:
        self.conn.executescript(script)

    async def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        self._check_not_nested()
        async with self._lock:
            return await self._thread(self._execute, sql, tuple(params))

    def _execute(self, sql: str, params: tuple[Any, ...]) -> sqlite3.Cursor:
        try:
            cursor = self.conn.execute(sql, params)
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            raise
        self.last_write_at = iso()
        return cursor

    async def executemany(self, sql: str, rows: Iterable[Sequence[Any]]) -> None:
        self._check_not_nested()
        async with self._lock:
            await self._thread(self._executemany, sql, list(rows))

    def _executemany(self, sql: str, rows: list[Sequence[Any]]) -> None:
        try:
            self.conn.executemany(sql, rows)
            self.conn.commit()
        except BaseException:
            self.conn.rollback()
            raise
        self.last_write_at = iso()

    async def fetchone(self, sql: str, params: Iterable[Any] = ()) -> dict[str, Any] | None:
        self._check_not_nested()
        async with self._lock:
            return await self._thread(self._fetchone, sql, tuple(params))

    def _fetchone(self, sql: str, params: tuple[Any, ...]) -> dict[str, Any] | None:
        cursor = self.conn.execute(sql, params)
        return cursor.fetchone()

    async def fetchall(self, sql: str, params: Iterable[Any] = ()) -> list[dict[str, Any]]:
        self._check_not_nested()
        async with self._lock:
            return await self._thread(self._fetchall, sql, tuple(params))

    def _fetchall(self, sql: str, params: tuple[Any, ...]) -> list[dict[str, Any]]:
        cursor = self.conn.execute(sql, params)
        return cursor.fetchall()

    async def scalar(self, sql: str, params: Iterable[Any] = ()) -> Any:
        row = await self.fetchone(sql, params)
        if not row:
            return None
        return next(iter(row.values()))

    # ------------------------------------------------------------------ meta
    async def get_meta(self, key: str, *, conn: sqlite3.Connection | None = None) -> str | None:
        if conn is not None:
            row = await self.fetchone_on(conn, "SELECT value FROM meta WHERE key = ?", (key,))
        else:
            row = await self.fetchone("SELECT value FROM meta WHERE key = ?", (key,))
        return row["value"] if row else None

    async def set_meta(self, key: str, value: str, *, conn: sqlite3.Connection | None = None) -> None:
        sql = (
            "INSERT INTO meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value"
        )
        if conn is not None:
            await self.execute_on(conn, sql, (key, value))
        else:
            await self.execute(sql, (key, value))

    async def delete_meta(self, key: str, *, conn: sqlite3.Connection | None = None) -> None:
        if conn is not None:
            await self.execute_on(conn, "DELETE FROM meta WHERE key = ?", (key,))
        else:
            await self.execute("DELETE FROM meta WHERE key = ?", (key,))

    # ------------------------------------------------------------------ health
    async def health_check(self) -> bool:
        try:
            row = await self.fetchone("SELECT 1 AS ok")
            return bool(row and row.get("ok") == 1)
        except Exception:
            log.exception("database health check failed")
            return False

    def size_bytes(self) -> int:
        try:
            return self.path.stat().st_size
        except OSError:
            return 0

    async def close(self) -> None:
        self._check_not_nested()
        async with self._lock:
            if self._conn is not None:
                await self._thread(self._conn.close)
                self._conn = None
