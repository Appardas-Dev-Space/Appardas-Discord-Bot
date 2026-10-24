from __future__ import annotations

from datetime import timedelta

import pytest
from aiohttp import web

from app.services.accounts import AccountsService
from app.util import iso, utcnow
from app.web.sessions import Sessions, id_hash, safe_next


async def test_sessions_hashed_persistent_and_current_role(db, cfg):
    store = Sessions(db, cfg)
    account = await AccountsService(db).by_discord_id(1)
    raw = await store.create(account, username="operator")
    row = await db.fetchone("SELECT * FROM sessions")
    assert row["id_hash"] == id_hash(raw) and raw not in str(row)
    assert len(raw) == 43 and row["csrf"] != raw
    await db.close()
    await db.connect(owner_ids=cfg.owner_ids)
    session = await store.lookup(raw)
    assert session.account.id == account.id and session.username == "operator"
    await db.execute("UPDATE accounts SET role = 'viewer', is_system = 0 WHERE id = ?", (account.id,))
    assert (await store.lookup(raw)).account.role == "viewer"
    await store.delete(raw)
    assert await store.lookup(raw) is None


@pytest.mark.parametrize("reason", ["idle", "absolute", "account_inactive"])
async def test_session_expiry_and_inactive_status(db, cfg, reason):
    store = Sessions(db, cfg)
    account = await AccountsService(db).by_discord_id(1)
    raw = await store.create(account)
    if reason == "idle":
        await db.execute("UPDATE sessions SET last_seen_at = ?", (iso(utcnow() - timedelta(days=31)),))
    elif reason == "absolute":
        await db.execute("UPDATE sessions SET created_at = ?", (iso(utcnow() - timedelta(days=91)),))
    else:
        await db.execute("UPDATE accounts SET status = 'suspended'")
    session, resolved_account, why = await store.resolve(raw)
    assert session is None and resolved_account.id == account.id and why == reason
    assert await db.scalar("SELECT COUNT(*) FROM sessions") == 0


async def test_touch_throttle_and_expiry_never_outlives_absolute(db, cfg):
    store = Sessions(db, cfg)
    account = await AccountsService(db).by_discord_id(1)
    raw = await store.create(account)
    original = await store.lookup(raw)
    written = db.last_write_at
    assert (await store.lookup(raw)).last_seen_at == original.last_seen_at
    assert db.last_write_at == written
    created = utcnow() - timedelta(days=89)
    await db.execute("UPDATE sessions SET created_at = ?, last_seen_at = ?", (iso(created), iso(utcnow() - timedelta(seconds=61))))
    session = await store.lookup(raw)
    assert session.expires_at == iso(created + timedelta(days=90))


async def test_state_is_hashed_single_use_and_expired_state_is_consumed(db, cfg):
    store = Sessions(db, cfg)
    state = await store.create_state("/dashboard/g/1/settings")
    row = await db.fetchone("SELECT * FROM oauth_states")
    assert row["state_hash"] == id_hash(state) and state not in str(row)
    assert await store.consume_state(state) == "/dashboard/g/1/settings"
    assert await store.consume_state(state) is None
    state = await store.create_state("//evil.invalid")
    await db.execute("UPDATE oauth_states SET expires_at = ?", (iso(utcnow() - timedelta(seconds=1)),))
    assert await store.consume_state(state) is None
    assert await db.scalar("SELECT COUNT(*) FROM oauth_states") == 0


async def test_prune_and_cookie_attributes(db, cfg):
    store = Sessions(db, cfg)
    account = await AccountsService(db).by_discord_id(1)
    raw = await store.create(account)
    await store.create_state("/dashboard")
    await db.execute("UPDATE sessions SET expires_at = ?", (iso(utcnow() - timedelta(days=1)),))
    await db.execute("UPDATE oauth_states SET expires_at = ?", (iso(utcnow() - timedelta(days=1)),))
    assert await store.prune() == 1
    response = web.Response()
    store.set_cookie(response, raw)
    cookie = response.cookies[store.cookie_name]
    assert cookie["httponly"] and cookie["samesite"] == "Lax" and cookie["path"] == "/"
    assert int(cookie["max-age"]) == 30 * 86400 and not cookie["domain"]


@pytest.mark.parametrize("value", ["//evil.test", "/%2f/evil.test", "/\\evil.test", "/%5cevil", "/ok%0aLocation:bad", "https://evil.test", "/" + "x" * 201, "/auth/callback", "/logout", "/login"])
def test_safe_next_rejects_redirect_tricks(value):
    assert safe_next(value) == "/dashboard"


def test_safe_next_preserves_local_guild_path():
    assert safe_next("/dashboard/g/1/settings?saved=prefix") == "/dashboard/g/1/settings?saved=prefix"
