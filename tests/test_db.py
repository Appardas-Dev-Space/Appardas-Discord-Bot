from __future__ import annotations

import asyncio
import sqlite3
import threading

import pytest

from app.db import Database, DatabaseError


async def test_schema_and_prgamas(db):
    row = await db.fetchone("PRAGMA journal_mode")
    assert row["journal_mode"].lower() == "wal"
    tables = await db.fetchall("SELECT name FROM sqlite_master WHERE type = 'table'")
    names = {row["name"] for row in tables}
    for expected in (
        "guilds",
        "guild_settings",
        "cog_state",
        "cog_errors",
        "announcements",
        "nudges",
        "nudge_optouts",
        "github_watches",
        "audit_log",
        "meta",
        "accounts",
        "account_guilds",
        "sessions",
        "oauth_states",
        "activity_log",
    ):
        assert expected in names


async def test_meta_round_trip(db):
    await db.set_meta("key", "value")
    assert await db.get_meta("key") == "value"
    await db.set_meta("key", "second")
    assert await db.get_meta("key") == "second"
    await db.delete_meta("key")
    assert await db.get_meta("key") is None


async def test_health_check(db):
    assert await db.health_check() is True
    await db.close()
    assert await db.health_check() is False


async def test_last_write_timestamp_moves(db):
    await db.execute("INSERT INTO meta (key, value) VALUES ('a', 'b')")
    assert db.last_write_at is not None


async def test_migration_and_failed_owner_seed_roll_back_together(tmp_path):
    path = tmp_path / "legacy.db"
    with sqlite3.connect(path) as conn:
        conn.executescript("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT); INSERT INTO meta VALUES ('schema_version','2'); INSERT INTO meta VALUES ('dash.jti.old','secret'); CREATE TABLE sessions (id TEXT PRIMARY KEY, user_id INTEGER); INSERT INTO sessions VALUES ('old',1);")
    database = Database(path)
    with pytest.raises(DatabaseError):
        await database.connect(owner_ids=())
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT user_id FROM sessions").fetchone() == (1,)
        assert conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone() == ("2",)
        assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'accounts'").fetchone() is None
    await database.connect(owner_ids=(1,))
    assert await database.get_meta("schema_version") == "4"
    assert await database.get_meta("dash.jti.old") is None
    assert await database.scalar("SELECT COUNT(*) FROM sessions") == 0
    await database.close()


async def test_transaction_rolls_back_and_cancels_without_notifications(db):
    callbacks = []
    entered = asyncio.Event()

    async def mutation():
        async with db.transaction() as conn:
            await db.execute_on(conn, "INSERT INTO meta VALUES ('cancelled','value')")
            db.after_commit(conn, lambda: callbacks.append("committed"))
            entered.set()
            await asyncio.Future()

    task = asyncio.create_task(mutation())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await db.get_meta("cancelled") is None and callbacks == []
    async with db.transaction() as conn:
        with pytest.raises(RuntimeError):
            await db.execute("SELECT 1")
        await db.execute_on(conn, "INSERT INTO meta VALUES ('committed','value')")
        db.after_commit(conn, lambda: callbacks.append("committed"))
    assert callbacks == ["committed"]


async def test_cancelled_workers_keep_database_lock_until_drained(db):
    started, release = threading.Event(), threading.Event()

    def slow():
        started.set()
        release.wait(5)

    async def work():
        async with db._lock:
            await db._thread(slow)

    task = asyncio.create_task(work())
    await asyncio.to_thread(started.wait, 2)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert db._lock.locked()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await db.health_check()
