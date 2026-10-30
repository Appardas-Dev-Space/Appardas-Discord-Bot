from __future__ import annotations

import json
import re
import sqlite3
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

from app.main import AppBot
from app.services.accounts import AccountError, AccountsService
from app.services.activity import ACTIVITY_ACTIONS
from app.util import iso
from app.web.sessions import Sessions
from app.web.status import create_app

USER_ID = 12345678901234567
TEMPORARY_PASSWORD = "Temporary-pass123!"


async def create_admin(accounts, owner, *, discord_user_id=USER_ID, guild_ids=()):
    return await accounts.create(
        actor=owner, discord_user_id=discord_user_id, username=f"admin-{discord_user_id}",
        temporary_password=TEMPORARY_PASSWORD, name="Test admin", role="admin", guild_ids=guild_ids,
    )


async def test_delete_account_removes_credentials_sessions_and_grants(db, cfg, settings_service):
    accounts = AccountsService(db)
    owner = await accounts.by_discord_id(1)
    target = await create_admin(accounts, owner, guild_ids=[1])
    other = await create_admin(accounts, owner, discord_user_id=USER_ID + 1, guild_ids=[1])
    sessions = Sessions(db, cfg)
    raw = await sessions.create(target)
    other_raw = await sessions.create(other)

    deleted, session_count = await accounts.delete(target.id, actor=owner)

    assert deleted.id == target.id and session_count == 1
    assert await accounts.get(target.id) is None
    assert await accounts.by_username(target.username) is None
    assert await accounts.authenticate(target.username, TEMPORARY_PASSWORD) is None
    assert await sessions.lookup(raw) is None
    assert await accounts.guilds(target.id) == []
    assert await sessions.lookup(other_raw) is not None
    assert await accounts.has_grant(other, 1)


async def test_delete_protects_appardas_admin_even_after_display_name_change(db, cfg):
    accounts = AccountsService(db)
    owner = await accounts.by_discord_id(1)
    protected = await accounts.by_username("appardas-admin")
    await accounts.update(protected.id, actor=owner, name="Renamed admin", note=None)
    raw = await Sessions(db, cfg).create(protected)
    await db.execute("UPDATE accounts SET is_system = 0 WHERE id = ?", (protected.id,))

    with pytest.raises(AccountError):
        await accounts.delete(protected.id, actor=owner)

    assert await accounts.get(protected.id) is not None
    assert await Sessions(db, cfg).lookup(raw) is not None


async def test_delete_rechecks_owner_permissions(db):
    accounts = AccountsService(db)
    owner = await accounts.by_discord_id(1)
    target = await create_admin(accounts, owner)

    with pytest.raises(PermissionError):
        await accounts.delete(target.id, actor=target)
    await db.execute("UPDATE accounts SET role = 'admin' WHERE id = ?", (owner.id,))
    with pytest.raises(PermissionError):
        await accounts.delete(target.id, actor=owner)

    assert await accounts.get(target.id) is not None


async def test_delete_account_and_cleanup_roll_back_together(db, cfg, settings_service):
    accounts = AccountsService(db)
    owner = await accounts.by_discord_id(1)
    target = await create_admin(accounts, owner, guild_ids=[1])
    raw = await Sessions(db, cfg).create(target)

    with pytest.raises(RuntimeError):
        async with db.transaction() as conn:
            await accounts.delete(target.id, actor=owner, conn=conn)
            raise RuntimeError("activity insert failed")

    assert await accounts.get(target.id) is not None
    assert await Sessions(db, cfg).lookup(raw) is not None
    assert await accounts.has_grant(target, 1)


@pytest.mark.parametrize("role", ["admin", "viewer"])
@pytest.mark.parametrize("status", ["active", "suspended", "revoked"])
async def test_delete_supports_all_sub_account_roles_and_statuses(db, role, status):
    accounts = AccountsService(db)
    owner = await accounts.by_discord_id(1)
    target = await create_admin(accounts, owner)
    await accounts.update(target.id, actor=owner, name=target.name, note=None, role=role)
    await accounts.set_status(target.id, status, actor=owner)
    deleted, count = await accounts.delete(target.id, actor=owner)
    assert deleted.role == role and deleted.status == status and count == 0
    assert await accounts.get(target.id) is None


async def test_delete_preserves_managed_owner_guards(db):
    accounts = AccountsService(db)
    owner = await accounts.by_discord_id(1)
    with pytest.raises(AccountError, match="BOT_OWNER_IDS"):
        await accounts.delete(owner.id, actor=owner)
    assert await accounts.get(owner.id) is not None


async def test_protection_uses_identity_not_an_ordinary_accounts_display_name(db):
    accounts = AccountsService(db)
    owner = await accounts.by_discord_id(1)
    target = await create_admin(accounts, owner)
    await accounts.update(target.id, actor=owner, name="Appardas Admin", note=None)
    await accounts.delete(target.id, actor=owner)
    assert await accounts.get(target.id) is None
    assert await accounts.by_username("appardas-admin") is not None


async def test_delete_preserves_history_and_other_accounts_foreign_keys(db, settings_service):
    accounts = AccountsService(db)
    owner = await accounts.by_discord_id(1)
    target = await create_admin(accounts, owner, guild_ids=[1])
    other = await create_admin(accounts, owner, discord_user_id=USER_ID + 1, guild_ids=[1])
    await db.execute("UPDATE accounts SET created_by = ? WHERE id = ?", (target.id, other.id))
    await db.execute("UPDATE account_guilds SET granted_by = ? WHERE account_id = ?", (target.id, other.id))
    await db.execute(
        "INSERT INTO activity_log (request_id, account_id, discord_user_id, account_label, role, action, outcome, created_at) "
        "VALUES ('historical', ?, ?, ?, 'admin', 'cog.toggle', 'ok', ?)",
        (target.id, target.discord_user_id, target.name, iso()),
    )
    await db.execute(
        "INSERT INTO audit_log (guild_id, actor_id, action, created_at) VALUES (1, ?, 'cog.toggle', ?)",
        (target.discord_user_id, iso()),
    )

    await accounts.delete(target.id, actor=owner)

    history = await db.fetchone("SELECT * FROM activity_log WHERE request_id = 'historical'")
    assert history["account_id"] is None
    assert history["account_label"] == target.name and history["discord_user_id"] == target.discord_user_id
    assert (await accounts.get(other.id)).created_by is None
    assert (await accounts.guilds(other.id))[0]["granted_by"] is None
    assert await accounts.has_grant(other, 1)
    assert await db.scalar("SELECT actor_id FROM audit_log WHERE action = 'cog.toggle'") == target.discord_user_id
    assert await db.scalar("SELECT COUNT(*) FROM guilds WHERE id = 1") == 1
    assert await db.fetchall("PRAGMA foreign_key_check") == []


async def test_deleted_identifiers_can_be_reused_without_reusing_account_id(db):
    accounts = AccountsService(db)
    owner = await accounts.by_discord_id(1)
    target = await create_admin(accounts, owner)
    await accounts.delete(target.id, actor=owner)
    replacement = await create_admin(accounts, owner)
    assert replacement.id > target.id
    assert replacement.username == target.username and replacement.discord_user_id == target.discord_user_id
    with pytest.raises(AccountError, match="not found"):
        await accounts.delete(target.id, actor=owner)
    assert await accounts.get(replacement.id) is not None


@pytest.fixture
async def web_bot(cfg):
    bot = AppBot(cfg)
    await bot.prepare()
    await bot.settings_service.ensure_guild(1, "Test Guild")
    bot._connection._guilds[1] = SimpleNamespace(id=1, name="Test Guild", text_channels=[])
    yield bot
    await bot.close()
    await bot.shutdown()


@pytest.fixture
async def web_client(web_bot):
    client = TestClient(TestServer(create_app(web_bot)))
    await client.start_server()
    yield client
    await client.close()


@pytest.fixture
async def deletion_owner(web_client, web_bot):
    owner = await web_bot.accounts.by_discord_id(1)
    raw = await web_bot.sessions.create(owner)
    web_client.session.cookie_jar.update_cookies({web_bot.sessions.cookie_name: raw}, web_client.make_url("/"))
    session = await web_bot.sessions.lookup(raw)
    return owner, session.csrf


@pytest.fixture
async def deletion_target(web_bot, deletion_owner):
    owner, _ = deletion_owner
    return await create_admin(web_bot.accounts, owner, guild_ids=[1])


async def issue_challenge(client, target, csrf):
    response = await client.post(
        f"/dashboard/admin/accounts/{target.id}/delete/confirmation", data={"csrf": csrf},
        headers={"Accept": "application/json"},
    )
    assert response.status == 200
    return await response.json()


def deletion_form(csrf, challenge):
    return {"csrf": csrf, "confirmation_token": challenge["confirmation_token"], "confirmation": challenge["characters"]}


async def test_account_pages_offer_delete_without_controls_for_protected_accounts(web_client, web_bot, deletion_owner, deletion_target):
    owner, _ = deletion_owner
    protected = await web_bot.accounts.by_username("appardas-admin")
    listing = await web_client.get("/dashboard/admin/accounts")
    assert listing.status == 200
    text = await listing.text()
    assert f'action="/dashboard/admin/accounts/{deletion_target.id}/delete/confirmation"' in text
    for target in (owner, protected):
        assert f'action="/dashboard/admin/accounts/{target.id}/delete/confirmation"' not in text
    assert text.count('<dialog id="delete-account-modal"') == 1
    detail = await web_client.get(f"/dashboard/admin/accounts/{deletion_target.id}")
    assert 'data-confirm-delete disabled>Confirm deletion' in await detail.text()
    assert 'aria-labelledby="delete-account-title"' in await detail.text()
    protected_detail = await web_client.get(f"/dashboard/admin/accounts/{protected.id}")
    assert "Appardas Admin is protected and cannot be deleted" in await protected_detail.text()
    assert 'data-delete-account="' not in await protected_detail.text()


async def test_deletion_challenge_is_fresh_and_does_not_delete_or_log_page_views(web_client, web_bot, deletion_owner, deletion_target):
    _, csrf = deletion_owner
    first = await issue_challenge(web_client, deletion_target, csrf)
    second = await issue_challenge(web_client, deletion_target, csrf)
    assert re.fullmatch(r"[A-HJ-NP-Z2-9]{8}", first["characters"])
    assert first["confirmation_token"] != second["confirmation_token"]
    assert first["expires_in"] == 300 and first["account_id"] == deletion_target.id
    assert first["account_name"] == deletion_target.name
    assert await web_bot.accounts.get(deletion_target.id) is not None
    assert await web_bot.db.scalar("SELECT COUNT(*) FROM activity_log WHERE action = 'account.delete'") == 0


async def test_confirmed_deletion_logs_safe_snapshot_and_rejects_replay_and_old_cookie(web_client, web_bot, deletion_owner, deletion_target):
    owner, csrf = deletion_owner
    raw = await web_bot.sessions.create(deletion_target)
    challenge = await issue_challenge(web_client, deletion_target, csrf)
    path = f"/dashboard/admin/accounts/{deletion_target.id}/delete"
    response = await web_client.post(path, data=deletion_form(csrf, challenge), headers={"Accept": "application/json"})
    assert response.status == 200
    payload = await response.json()
    assert payload["redirect"] == "/dashboard/admin/accounts?saved=deleted"
    assert await web_bot.accounts.get(deletion_target.id) is None
    assert await web_bot.sessions.lookup(raw) is None
    assert await web_bot.accounts.guilds(deletion_target.id) == []
    activity = await web_bot.db.fetchone("SELECT * FROM activity_log WHERE action = 'account.delete'")
    assert activity["account_id"] == owner.id and activity["target"] == str(deletion_target.id)
    assert json.loads(activity["before_json"])["username"] == deletion_target.username
    assert json.loads(activity["detail"]) == {"sessions_deleted": 1, "grants_deleted": 1}
    assert TEMPORARY_PASSWORD not in str(activity) and "pbkdf2_sha256$" not in str(activity)
    assert challenge["characters"] not in str(activity) and challenge["confirmation_token"] not in str(activity)
    assert "account.delete" in ACTIVITY_ACTIONS
    saved = await web_client.get(payload["redirect"])
    assert saved.status == 200 and "Account deleted." in await saved.text()
    assert (await web_client.post(path, data=deletion_form(csrf, challenge), headers={"Accept": "application/json"})).status == 404
    assert (await web_client.get(f"/dashboard/admin/accounts/{deletion_target.id}")).status == 404
    web_client.session.cookie_jar.update_cookies({web_bot.sessions.cookie_name: raw}, web_client.make_url("/"))
    assert (await web_client.get("/dashboard/status.json")).status == 401


@pytest.mark.parametrize("confirmation", ["", "WRONG123", "abcdefgh", "é" * 8, "A" * 9])
async def test_deletion_requires_exact_characters_on_server(web_client, web_bot, deletion_owner, deletion_target, confirmation):
    _, csrf = deletion_owner
    raw = await web_bot.sessions.create(deletion_target)
    challenge = await issue_challenge(web_client, deletion_target, csrf)
    form = deletion_form(csrf, challenge)
    form["confirmation"] = confirmation
    response = await web_client.post(f"/dashboard/admin/accounts/{deletion_target.id}/delete", data=form, headers={"Accept": "application/json"})
    assert response.status == 400 and (await response.json())["field"] == "confirmation"
    assert await web_bot.accounts.get(deletion_target.id) is not None
    assert await web_bot.sessions.lookup(raw) is not None
    assert await web_bot.accounts.has_grant(deletion_target, 1)


@pytest.mark.parametrize("invalid", ["missing", "forged", "tampered", "expired", "restarted"])
async def test_deletion_rejects_invalid_challenge(web_client, web_bot, deletion_owner, deletion_target, monkeypatch, invalid):
    from app.web import accounts as routes

    _, csrf = deletion_owner
    challenge = await issue_challenge(web_client, deletion_target, csrf)
    form = deletion_form(csrf, challenge)
    if invalid == "missing":
        del form["confirmation_token"]
    elif invalid == "forged":
        form["confirmation_token"] = f"999999999999.{challenge['characters']}.{'0' * 64}"
    elif invalid == "tampered":
        expires, characters, signature = form["confirmation_token"].split(".")
        characters = ("A" if characters[0] != "A" else "B") + characters[1:]
        form["confirmation_token"] = f"{expires}.{characters}.{signature}"
        form["confirmation"] = characters
    elif invalid == "expired":
        expires = int(form["confirmation_token"].split(".")[0])
        monkeypatch.setattr(routes.time, "time", lambda: expires)
    else:
        monkeypatch.setitem(web_client.server.app, "account_deletion_key", b"a-new-process-key")
    response = await web_client.post(f"/dashboard/admin/accounts/{deletion_target.id}/delete", data=form, headers={"Accept": "application/json"})
    assert response.status == 400 and (await response.json())["field"] == "confirmation"
    assert await web_bot.accounts.get(deletion_target.id) is not None


async def test_deletion_challenge_is_bound_to_target_and_session(web_client, web_bot, deletion_owner, deletion_target):
    owner, csrf = deletion_owner
    other = await create_admin(web_bot.accounts, owner, discord_user_id=USER_ID + 1)
    challenge = await issue_challenge(web_client, deletion_target, csrf)
    wrong_target = await web_client.post(f"/dashboard/admin/accounts/{other.id}/delete", data=deletion_form(csrf, challenge), headers={"Accept": "application/json"})
    assert wrong_target.status == 400
    raw = await web_bot.sessions.create(owner)
    web_client.session.cookie_jar.update_cookies({web_bot.sessions.cookie_name: raw}, web_client.make_url("/"))
    csrf = (await web_bot.sessions.lookup(raw)).csrf
    wrong_session = await web_client.post(f"/dashboard/admin/accounts/{deletion_target.id}/delete", data=deletion_form(csrf, challenge), headers={"Accept": "application/json"})
    assert wrong_session.status == 400
    assert await web_bot.accounts.get(deletion_target.id) is not None
    assert await web_bot.accounts.get(other.id) is not None


@pytest.mark.parametrize("suffix", ["/delete/confirmation", "/delete"])
async def test_deletion_requires_csrf_and_protects_system_accounts(web_client, web_bot, deletion_owner, deletion_target, suffix):
    owner, csrf = deletion_owner
    forged = await web_client.post(f"/dashboard/admin/accounts/{deletion_target.id}{suffix}", data={"csrf": "wrong"}, headers={"Accept": "application/json"})
    assert forged.status == 403
    protected = await web_bot.accounts.by_username("appardas-admin")
    for target in (owner, protected):
        response = await web_client.post(f"/dashboard/admin/accounts/{target.id}{suffix}", data={"csrf": csrf}, headers={"Accept": "application/json"})
        assert response.status == 400 and (await response.json())["field"] == "account"
        assert await web_bot.accounts.get(target.id) is not None
    assert await web_bot.accounts.get(deletion_target.id) is not None


@pytest.mark.parametrize("role", ["admin", "viewer"])
async def test_non_owners_cannot_generate_or_submit_deletion(web_client, web_bot, deletion_owner, deletion_target, role):
    owner, _ = deletion_owner
    member = await create_admin(web_bot.accounts, owner, discord_user_id=USER_ID + 1)
    member = await web_bot.accounts.update(member.id, actor=owner, name=member.name, note=None, role=role)
    member = await web_bot.accounts.change_password(member.id, TEMPORARY_PASSWORD, "Permanent-pass123!")
    raw = await web_bot.sessions.create(member)
    web_client.session.cookie_jar.update_cookies({web_bot.sessions.cookie_name: raw}, web_client.make_url("/"))
    csrf = (await web_bot.sessions.lookup(raw)).csrf
    for suffix in ("/delete/confirmation", "/delete"):
        response = await web_client.post(f"/dashboard/admin/accounts/{deletion_target.id}{suffix}", data={"csrf": csrf}, headers={"Accept": "application/json"})
        assert response.status == 404
    assert await web_bot.accounts.get(deletion_target.id) is not None


async def test_deletion_rechecks_owner_after_challenge_is_issued(web_client, web_bot, deletion_owner, deletion_target):
    owner, csrf = deletion_owner
    challenge = await issue_challenge(web_client, deletion_target, csrf)
    await web_bot.db.execute("UPDATE accounts SET role = 'admin' WHERE id = ?", (owner.id,))
    response = await web_client.post(f"/dashboard/admin/accounts/{deletion_target.id}/delete", data=deletion_form(csrf, challenge), headers={"Accept": "application/json"})
    assert response.status == 404
    assert await web_bot.accounts.get(deletion_target.id) is not None


async def test_deletion_rolls_back_cleanup_if_activity_write_fails(web_client, web_bot, deletion_owner, deletion_target, monkeypatch):
    _, csrf = deletion_owner
    raw = await web_bot.sessions.create(deletion_target)
    challenge = await issue_challenge(web_client, deletion_target, csrf)

    async def unavailable(**kwargs):
        raise sqlite3.OperationalError("activity unavailable")

    with monkeypatch.context() as patch:
        patch.setattr("app.web.accounts.record_activity", unavailable)
        response = await web_client.post(f"/dashboard/admin/accounts/{deletion_target.id}/delete", data=deletion_form(csrf, challenge), headers={"Accept": "application/json"})
    assert response.status == 503
    assert await web_bot.accounts.get(deletion_target.id) is not None
    assert await web_bot.sessions.lookup(raw) is not None
    assert await web_bot.accounts.has_grant(deletion_target, 1)
    assert await web_bot.db.scalar("SELECT COUNT(*) FROM activity_log WHERE action = 'account.delete'") == 0
    retry = await web_client.post(f"/dashboard/admin/accounts/{deletion_target.id}/delete", data=deletion_form(csrf, challenge), headers={"Accept": "application/json"})
    assert retry.status == 200


async def test_deletion_has_working_html_confirmation_and_error_retry(web_client, web_bot, deletion_owner, deletion_target):
    _, csrf = deletion_owner
    response = await web_client.post(f"/dashboard/admin/accounts/{deletion_target.id}/delete/confirmation", data={"csrf": csrf})
    assert response.status == 200 and response.headers["Cache-Control"] == "no-store"
    text = await response.text()
    token = re.search(r'name="confirmation_token" value="([^"]+)"', text).group(1)
    assert '<code data-confirmation-characters>' in text and "Confirm deletion" in text
    bad = await web_client.post(f"/dashboard/admin/accounts/{deletion_target.id}/delete", data={"csrf": csrf, "confirmation_token": token, "confirmation": "wrong"})
    assert bad.status == 400
    retry_text = await bad.text()
    assert "do not match" in retry_text
    token = re.search(r'name="confirmation_token" value="([^"]+)"', retry_text).group(1)
    response = await web_client.post(f"/dashboard/admin/accounts/{deletion_target.id}/delete", data={"csrf": csrf, "confirmation_token": token, "confirmation": token.split(".")[1]}, allow_redirects=False)
    assert response.status == 303 and response.headers["Location"] == "/dashboard/admin/accounts?saved=deleted"
    assert await web_bot.accounts.get(deletion_target.id) is None


@pytest.mark.parametrize("account_id", ["999999", "-1"])
@pytest.mark.parametrize("suffix", ["/delete/confirmation", "/delete"])
async def test_deletion_returns_not_found_for_missing_targets(web_client, deletion_owner, account_id, suffix):
    _, csrf = deletion_owner
    response = await web_client.post(f"/dashboard/admin/accounts/{account_id}{suffix}", data={"csrf": csrf}, headers={"Accept": "application/json"})
    assert response.status == 404


@pytest.mark.parametrize("suffix", ["/delete/confirmation", "/delete"])
async def test_anonymous_requests_cannot_generate_or_submit_deletion(web_client, web_bot, deletion_target, suffix):
    web_client.session.cookie_jar.clear()
    response = await web_client.post(f"/dashboard/admin/accounts/{deletion_target.id}{suffix}", data={}, headers={"Accept": "application/json"})
    assert response.status == 401
    assert await web_bot.accounts.get(deletion_target.id) is not None


async def test_appardas_admin_can_delete_other_accounts_but_remains_protected(web_client, web_bot, deletion_target):
    protected = await web_bot.accounts.by_username("appardas-admin")
    protected = await web_bot.accounts.change_password(protected.id, "admin", "Permanent-admin123!")
    raw = await web_bot.sessions.create(protected)
    web_client.session.cookie_jar.update_cookies({web_bot.sessions.cookie_name: raw}, web_client.make_url("/"))
    csrf = (await web_bot.sessions.lookup(raw)).csrf
    challenge = await issue_challenge(web_client, deletion_target, csrf)
    response = await web_client.post(f"/dashboard/admin/accounts/{deletion_target.id}/delete", data=deletion_form(csrf, challenge), headers={"Accept": "application/json"})
    assert response.status == 200
    assert await web_bot.accounts.get(deletion_target.id) is None
    assert (await web_bot.accounts.get(protected.id)).deletion_block_reason
    assert await web_bot.sessions.lookup(raw) is not None


async def test_deletion_confirmation_escapes_account_name_in_markup(web_client, web_bot, deletion_owner, deletion_target):
    owner, csrf = deletion_owner
    name = 'Ana "<script>alert(1)</script> & Co'
    await web_bot.accounts.update(deletion_target.id, actor=owner, name=name, note=None)
    listing = await web_client.get("/dashboard/admin/accounts")
    text = await listing.text()
    assert name not in text and '&lt;script&gt;alert(1)&lt;/script&gt;' in text
    challenge = await issue_challenge(web_client, deletion_target, csrf)
    assert challenge["account_name"] == name
    response = await web_client.post(f"/dashboard/admin/accounts/{deletion_target.id}/delete/confirmation", data={"csrf": csrf})
    text = await response.text()
    assert name not in text and '&lt;script&gt;alert(1)&lt;/script&gt;' in text
