from __future__ import annotations

import sqlite3
from datetime import timedelta

import pytest

from app.db import SCHEMA_PATH, Database, DatabaseError
from app.services.accounts import AccountsService, hash_password, verify_password
from app.util import iso, utcnow
from app.web.sessions import Sessions, id_hash

DISCORD_ID = 223456789012345678
LEGACY_PASSWORD = "Legacy-pass123!"
LEGACY_SESSION = "s" * 43


def legacy_database(path, version, *, username="Legacy Ána"):
    """Reproduce the account/session column differences in supported v2 and v3 schemas."""
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    discord_column = "INTEGER NOT NULL UNIQUE" if version == 2 else "INTEGER UNIQUE"
    schema = schema.replace("discord_user_id TEXT UNIQUE", f"discord_user_id {discord_column}")
    schema = schema.replace("CREATE UNIQUE INDEX IF NOT EXISTS idx_accounts_username_ci ON accounts (lower(username)) WHERE username IS NOT NULL;\n", "")
    schema = schema.replace("VALUES ('schema_version', '4')", f"VALUES ('schema_version', '{version}')")
    if version == 2:
        for line in (
            "  username        TEXT UNIQUE,\n",
            "  password_hash   TEXT,\n",
            "  must_change_password INTEGER NOT NULL DEFAULT 0,\n",
            "CREATE INDEX IF NOT EXISTS idx_accounts_username ON accounts (username);\n",
        ):
            schema = schema.replace(line, "")
        schema = schema.replace("  discord_user_id INTEGER,", "  discord_user_id INTEGER NOT NULL,", 1)
    now = utcnow()
    password_hash = hash_password(LEGACY_PASSWORD) if version == 3 else None
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(schema)
        conn.execute(
            "INSERT INTO accounts (id, discord_user_id, name, role, is_system, created_at, updated_at) VALUES (5, 1, 'Operator Owner', 'owner', 1, ?, ?)",
            (iso(now), iso(now)),
        )
        conn.execute(
            "INSERT INTO accounts (id, discord_user_id, name, role, note, created_by, created_at, updated_at, last_login_at, last_login_ip) VALUES (8, ?, 'Legacy Ana', 'admin', 'Preserve history', 5, ?, ?, ?, '127.0.0.1')",
            (DISCORD_ID, iso(now), iso(now), iso(now)),
        )
        if version == 3:
            conn.execute("UPDATE accounts SET username = ?, password_hash = ?, must_change_password = 1 WHERE id = 8", (username, password_hash))
        conn.execute("INSERT INTO guilds (id, name, joined_at) VALUES (1, 'Test Guild', ?)", (iso(now),))
        conn.execute("INSERT INTO account_guilds (account_id, guild_id, granted_by, granted_at) VALUES (8, 1, 5, ?)", (iso(now),))
        conn.execute(
            "INSERT INTO sessions (id_hash, account_id, discord_user_id, username, csrf, created_at, last_seen_at, expires_at) VALUES (?, 8, ?, ?, ?, ?, ?, ?)",
            (id_hash(LEGACY_SESSION), DISCORD_ID, username, "c" * 43, iso(now), iso(now), iso(now + timedelta(days=30))),
        )
        conn.execute(
            "INSERT INTO activity_log (request_id, account_id, discord_user_id, account_label, role, action, outcome, created_at) VALUES ('legacy-request', 8, ?, 'Legacy Ana', 'admin', 'account.create', 'ok', ?)",
            (DISCORD_ID, iso(now)),
        )
        # Keep a deleted identifier's sequence high-water mark through the rebuild.
        conn.execute("INSERT INTO accounts (id, discord_user_id, name, created_at, updated_at) VALUES (40, ?, 'Removed', ?, ?)", (DISCORD_ID + 1, iso(now), iso(now)))
        conn.execute("DELETE FROM accounts WHERE id = 40")
        return dict(conn.execute("SELECT * FROM accounts WHERE id = 8").fetchone()), password_hash


@pytest.mark.parametrize("version", [2, 3])
async def test_account_migration_preserves_identity_credentials_grants_and_history(tmp_path, cfg, version):
    path = tmp_path / f"legacy-v{version}.db"
    before, password_hash = legacy_database(path, version)
    database = Database(path)
    await database.connect(owner_ids=(1,))
    try:
        after = await database.fetchone("SELECT *, typeof(discord_user_id) AS discord_type FROM accounts WHERE id = 8")
        assert after["discord_type"] == "text" and after["discord_user_id"] == str(DISCORD_ID)
        for key in before.keys() - {"discord_user_id"}:
            assert after[key] == before[key]
        assert await database.get_meta("schema_version") == "4"
        assert await database.scalar("PRAGMA foreign_keys") == 1
        assert await database.fetchone("PRAGMA foreign_key_check") is None
        assert (await database.fetchone("SELECT * FROM account_guilds WHERE account_id = 8"))["granted_by"] == 5
        assert (await database.fetchone("SELECT * FROM activity_log WHERE request_id = 'legacy-request'"))["account_id"] == 8
        accounts = AccountsService(database)
        assert (await accounts.by_discord_id(1)).id == 5
        assert (await accounts.by_discord_id(1)).name == "Operator Owner"
        assert (await accounts.by_username("appardas-admin")).id > 40
        if version == 3:
            assert after["password_hash"] == password_hash and verify_password(LEGACY_PASSWORD, password_hash)
            session = await Sessions(database, cfg).lookup(LEGACY_SESSION, touch=False)
            assert session.account.id == 8 and session.account.discord_user_id == DISCORD_ID
            assert (await accounts.authenticate(" Legacy Ána ", LEGACY_PASSWORD)).id == 8
        else:
            assert after["username"] is None and after["password_hash"] is None
            assert after["must_change_password"] == 0
            assert await database.scalar("SELECT COUNT(*) FROM sessions") == 0
        count = await database.scalar("SELECT COUNT(*) FROM accounts")
        await database.close()
        await database.connect(owner_ids=(1,))
        assert await database.scalar("SELECT COUNT(*) FROM accounts") == count
        assert (await accounts.by_discord_id(DISCORD_ID)).id == 8
        assert await database.fetchone("PRAGMA foreign_key_check") is None
    finally:
        await database.close()


async def test_account_rebuild_and_credential_columns_roll_back_on_failed_bootstrap(tmp_path):
    path = tmp_path / "legacy-v2.db"
    before, _ = legacy_database(path, 2)
    database = Database(path)
    with pytest.raises(DatabaseError):
        await database.connect(owner_ids=())
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        assert dict(conn.execute("SELECT * FROM accounts WHERE id = 8").fetchone()) == before
        columns = {row["name"]: row for row in conn.execute("PRAGMA table_info(accounts)")}
        assert "username" not in columns and "password_hash" not in columns
        assert columns["discord_user_id"]["type"] == "INTEGER" and columns["discord_user_id"]["notnull"] == 1
        assert conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0] == "2"
        assert conn.execute("SELECT account_id FROM sessions").fetchone()[0] == 8
        assert conn.execute("SELECT granted_by FROM account_guilds").fetchone()[0] == 5
        assert conn.execute("PRAGMA foreign_key_check").fetchone() is None
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'accounts_v4_new'").fetchone() is None
    await database.connect(owner_ids=(1,))
    assert await database.get_meta("schema_version") == "4"
    await database.close()


async def test_case_insensitive_username_collision_rolls_back_without_renaming_accounts(tmp_path):
    path = tmp_path / "legacy-collision.db"
    _, password_hash = legacy_database(path, 3, username="ana")
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute(
            "INSERT INTO accounts (id, discord_user_id, username, password_hash, name, role, created_by, created_at, updated_at) VALUES (9, ?, 'ANA', ?, 'Other Ana', 'viewer', 5, ?, ?)",
            (DISCORD_ID + 2, password_hash, iso(), iso()),
        )
        before = [dict(row) for row in conn.execute("SELECT * FROM accounts ORDER BY id")]
    database = Database(path)
    with pytest.raises(DatabaseError):
        await database.connect(owner_ids=(1,))
    with sqlite3.connect(path) as conn:
        conn.row_factory = sqlite3.Row
        assert [dict(row) for row in conn.execute("SELECT * FROM accounts ORDER BY id")] == before
        assert conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()[0] == "3"
        assert conn.execute("SELECT account_id FROM sessions").fetchone()[0] == 8
        assert conn.execute("PRAGMA foreign_key_check").fetchone() is None
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'accounts_v4_new'").fetchone() is None
