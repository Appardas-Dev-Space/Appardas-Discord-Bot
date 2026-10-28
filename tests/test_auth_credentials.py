from __future__ import annotations

import asyncio
import json
import re
import sqlite3
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest
from aiohttp.test_utils import TestClient, TestServer

from app.main import AppBot
from app.services.accounts import hash_password, verify_password
from app.util import iso
from app.web.status import create_app

SUB_ACCOUNT_ID = "223456789012345678"
TEMPORARY_PASSWORD = "Private-temp123!"


async def owner_csrf(client, bot):
    owner = await bot.accounts.by_discord_id(1)
    raw = await bot.sessions.create(owner)
    client.session.cookie_jar.update_cookies({bot.sessions.cookie_name: raw}, client.make_url("/"))
    page = await client.get("/dashboard/admin/accounts/new")
    assert page.status == 200
    return csrf_from(await page.text())


def sub_account_form(csrf):
    return {
        "csrf": csrf, "discord_user_id": SUB_ACCOUNT_ID, "username": "Operator.One", "temporary_password": TEMPORARY_PASSWORD,
        "name": "Operator One", "role": "viewer", "guilds": "1",
    }


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match, "page did not contain a CSRF field"
    return match.group(1)


@pytest.fixture
async def bot(cfg):
    instance = AppBot(cfg)
    await instance.prepare()
    await instance.settings_service.ensure_guild(1, "Test Guild")
    instance._connection._guilds[1] = SimpleNamespace(
        id=1,
        name="Test Guild",
        text_channels=[SimpleNamespace(id=10, name="general")],
    )
    recipient = SimpleNamespace(id=int(SUB_ACCOUNT_ID), name="new-account-user", send=AsyncMock())
    instance.fetch_user = AsyncMock(return_value=recipient)
    yield instance
    await instance.close()
    await instance.shutdown()


@pytest.fixture
async def client(bot):
    instance = TestClient(TestServer(create_app(bot)))
    await instance.start_server()
    yield instance
    await instance.close()


async def test_password_hash_and_verify():
    h = hash_password("secret123")
    assert h.startswith("pbkdf2_sha256$100000$")
    assert verify_password("secret123", h)
    assert not verify_password("wrong", h)
    assert not verify_password("secret123", "invalid$hash")
    assert not verify_password("secret123", None)


async def test_preliminary_admin_bootstrapped(bot):
    accounts = bot.accounts
    admin = await accounts.by_username("appardas-admin")
    assert admin is not None
    assert admin.role == "owner"
    assert admin.status == "active"
    assert admin.is_system is True
    assert admin.must_change_password is True

    # Check authentication
    authenticated = await accounts.authenticate("appardas-admin", "admin")
    assert authenticated is not None
    assert authenticated.id == admin.id
    assert authenticated.last_login_at is not None

    # Failed authentication
    assert await accounts.authenticate("appardas-admin", "wrongpassword") is None
    assert await accounts.authenticate("nonexistent", "admin") is None


async def test_login_flow_and_mandatory_password_change(client, bot):
    # GET /login renders login form
    login_get = await client.get("/login")
    assert login_get.status == 200
    text = await login_get.text()
    assert "Sign in" in text
    assert 'name="username"' in text
    assert 'name="password"' in text
    assert "Add to Discord" not in text

    # POST /login with invalid credentials -> 401
    bad_login = await client.post("/login", data={"username": "appardas-admin", "password": "wrong"})
    assert bad_login.status == 401
    assert "Invalid username or password" in await bad_login.text()

    # POST /login with empty credentials -> 400
    empty_login = await client.post("/login", data={"username": "", "password": ""})
    assert empty_login.status == 400

    # POST /login with valid preliminary credentials -> 302 redirecting to /change-password
    login_post = await client.post(
        "/login",
        data={"username": "appardas-admin", "password": "admin"},
        allow_redirects=False,
    )
    assert login_post.status == 302
    assert login_post.headers["Location"] == "/change-password"
    assert bot.sessions.cookie_name in client.session.cookie_jar.filter_cookies(client.make_url("/")).keys()

    # Navigating to /dashboard while must_change_password=True redirects to /change-password
    dashboard_req = await client.get("/dashboard", allow_redirects=False)
    assert dashboard_req.status == 302
    assert dashboard_req.headers["Location"] == "/change-password"

    # API request to /dashboard/status.json returns 403 password_change_required
    api_req = await client.get("/dashboard/status.json")
    assert api_req.status == 403
    assert (await api_req.json())["error"] == "password_change_required"

    # GET /change-password renders change password form
    change_get = await client.get("/change-password")
    assert change_get.status == 200
    change_text = await change_get.text()
    assert "Change password" in change_text
    csrf = csrf_from(change_text)

    # POST /change-password validation checks
    # 1. Mismatch
    mismatch = await client.post(
        "/change-password",
        data={
            "csrf": csrf,
            "current_password": "admin",
            "new_password": "newpassword1",
            "confirm_password": "differentpassword",
        },
    )
    assert mismatch.status == 400
    assert "do not match" in await mismatch.text()

    # 2. Too short (<6 chars)
    short = await client.post(
        "/change-password",
        data={
            "csrf": csrf,
            "current_password": "admin",
            "new_password": "123",
            "confirm_password": "123",
        },
    )
    assert short.status == 400
    assert "at least 6 characters" in await short.text()

    # 3. Reusing default "admin"
    reuse = await client.post(
        "/change-password",
        data={
            "csrf": csrf,
            "current_password": "admin",
            "new_password": "admin",
            "confirm_password": "admin",
        },
    )
    assert reuse.status == 400
    assert "cannot be the default password" in await reuse.text()

    # 4. Wrong current password
    wrong_curr = await client.post(
        "/change-password",
        data={
            "csrf": csrf,
            "current_password": "wrongpassword",
            "new_password": "newsecurepass123",
            "confirm_password": "newsecurepass123",
        },
    )
    assert wrong_curr.status == 400
    assert "Current password does not match" in await wrong_curr.text()

    # 5. Successful password change
    success = await client.post(
        "/change-password",
        data={
            "csrf": csrf,
            "current_password": "admin",
            "new_password": "newsecurepass123",
            "confirm_password": "newsecurepass123",
        },
        allow_redirects=False,
    )
    assert success.status in (302, 303)
    assert success.headers["Location"] == "/dashboard"

    # Now /dashboard is accessible!
    dash = await client.get("/dashboard")
    assert dash.status == 200
    assert "Servers" in await dash.text()

    # Database account has must_change_password=0
    admin = await bot.accounts.by_username("appardas-admin")
    assert admin.must_change_password is False

    # Old password no longer works
    assert await bot.accounts.authenticate("appardas-admin", "admin") is None
    # New password works
    assert await bot.accounts.authenticate("appardas-admin", "newsecurepass123") is not None


async def test_admin_creation_to_local_sign_in_and_first_password_change(client, bot):
    csrf = await owner_csrf(client, bot)
    form_page = await client.get("/dashboard/admin/accounts/new")
    form_html = await form_page.text()
    assert 'name="username"' in form_html
    assert 'name="temporary_password" required minlength="6"' in form_html
    created = await client.post("/dashboard/admin/accounts", data=sub_account_form(csrf), allow_redirects=False)
    assert created.status == 303
    account = await bot.accounts.by_username("operator.one")
    assert account.discord_user_id == int(SUB_ACCOUNT_ID) and account.must_change_password
    detail = await client.get(created.headers["Location"])
    detail_html = await detail.text()
    assert "operator.one" in detail_html and "username and password, not Discord" in detail_html
    assert f'data-copy="{SUB_ACCOUNT_ID}"' in detail_html
    assert TEMPORARY_PASSWORD not in detail_html and "pbkdf2_sha256$" not in detail_html
    activity = await bot.db.fetchone("SELECT * FROM activity_log WHERE action = 'account.create'")
    assert json.loads(activity["after_json"])["discord_user_id"] == SUB_ACCOUNT_ID
    assert TEMPORARY_PASSWORD not in str(activity) and "pbkdf2_sha256$" not in str(activity)

    member = TestClient(TestServer(create_app(bot)))
    await member.start_server()
    try:
        discord_login = await member.post("/login", data={"username": SUB_ACCOUNT_ID, "password": TEMPORARY_PASSWORD})
        assert discord_login.status == 401
        login = await member.post("/login", data={"username": " OPERATOR.ONE ", "password": TEMPORARY_PASSWORD}, allow_redirects=False)
        assert login.status == 302 and login.headers["Location"] == "/change-password"
        blocked = await member.get("/dashboard/status.json")
        assert blocked.status == 403 and (await blocked.json())["error"] == "password_change_required"
        blocked_page = await member.get("/dashboard", allow_redirects=False)
        assert blocked_page.headers["Location"] == "/change-password"
        password_page = await member.get("/change-password")
        member_csrf = csrf_from(await password_page.text())
        changed = await member.post("/change-password", data={
            "csrf": member_csrf, "current_password": TEMPORARY_PASSWORD,
            "new_password": "Permanent-pass123!", "confirm_password": "Permanent-pass123!",
        }, allow_redirects=False)
        assert changed.status == 303 and changed.headers["Location"] == "/dashboard"
        assert (await member.get("/dashboard/g/1")).status == 200
        assert (await member.get("/dashboard/admin/accounts")).status == 404
        denied = await member.post("/dashboard/g/1/cogs/dev", data={"csrf": member_csrf, "enabled": "0"}, headers={"Accept": "application/json"})
        assert denied.status == 403
    finally:
        await member.close()
    changed_account = await bot.accounts.by_username("operator.one")
    assert changed_account.id == account.id and changed_account.discord_user_id == int(SUB_ACCOUNT_ID)
    assert not changed_account.must_change_password and changed_account.role == "viewer"
    assert [row["id"] for row in await bot.accounts.guilds(account.id)] == [1]
    assert await bot.accounts.authenticate("operator.one", TEMPORARY_PASSWORD) is None
    assert (await bot.accounts.authenticate("operator.one", "Permanent-pass123!")).id == account.id


@pytest.mark.parametrize("field", ["discord_user_id", "username", "temporary_password"])
async def test_admin_creation_requires_all_credentials_without_echoing_password(client, bot, field):
    csrf = await owner_csrf(client, bot)
    form = sub_account_form(csrf)
    del form[field]
    count = await bot.db.scalar("SELECT COUNT(*) FROM accounts")
    response = await client.post("/dashboard/admin/accounts", data=form)
    assert response.status == 400
    assert TEMPORARY_PASSWORD not in await response.text()
    assert await bot.db.scalar("SELECT COUNT(*) FROM accounts") == count
    assert await bot.accounts.by_discord_id(SUB_ACCOUNT_ID) is None
    assert await bot.db.scalar("SELECT COUNT(*) FROM account_guilds") == 0
    bot.fetch_user.assert_not_awaited()
    bot.fetch_user.return_value.send.assert_not_awaited()


async def test_owner_assigns_legacy_credentials_and_resets_sessions_without_data_loss(client, bot):
    csrf = await owner_csrf(client, bot)
    owner = await bot.accounts.by_discord_id(1)
    now = iso()
    cursor = await bot.db.execute(
        "INSERT INTO accounts (discord_user_id, name, role, note, created_by, created_at, updated_at) VALUES (?, 'Legacy Operator', 'admin', 'Retain this note', ?, ?, ?)",
        (SUB_ACCOUNT_ID, owner.id, now, now),
    )
    account_id = cursor.lastrowid
    await bot.accounts.set_grants(account_id, [1], actor=owner)
    count = await bot.db.scalar("SELECT COUNT(*) FROM accounts")
    assigned = await client.post(f"/dashboard/admin/accounts/{account_id}/credentials", data={
        "csrf": csrf, "username": "legacy-operator", "temporary_password": TEMPORARY_PASSWORD,
    }, headers={"Accept": "application/json"})
    assert assigned.status == 200 and TEMPORARY_PASSWORD not in await assigned.text()
    account = await bot.accounts.authenticate("legacy-operator", TEMPORARY_PASSWORD)
    assert account.id == account_id and account.must_change_password
    account = await bot.accounts.change_password(account_id, TEMPORARY_PASSWORD, "Permanent-pass123!")
    raw = await bot.sessions.create(account)
    reset = await client.post(f"/dashboard/admin/accounts/{account_id}/credentials", data={
        "csrf": csrf, "username": "legacy-operator", "temporary_password": "Reset-pass123!",
    }, headers={"Accept": "application/json"})
    assert reset.status == 200 and "Reset-pass123!" not in await reset.text()
    assert await bot.sessions.lookup(raw) is None
    assert await bot.accounts.authenticate("legacy-operator", "Permanent-pass123!") is None
    reset_account = await bot.accounts.authenticate("legacy-operator", "Reset-pass123!")
    assert reset_account.id == account_id and reset_account.must_change_password
    assert reset_account.discord_user_id == int(SUB_ACCOUNT_ID) and reset_account.role == "admin" and reset_account.status == "active"
    assert reset_account.note == "Retain this note" and reset_account.created_by == owner.id
    assert await bot.db.scalar("SELECT COUNT(*) FROM accounts") == count
    assert [row["id"] for row in await bot.accounts.guilds(account_id)] == [1]
    activity_rows = await bot.db.fetchall("SELECT * FROM activity_log WHERE action = 'account.credentials.reset' ORDER BY id")
    assert len(activity_rows) == 2 and json.loads(activity_rows[-1]["detail"])["sessions_deleted"] == 1
    assert TEMPORARY_PASSWORD not in str(activity_rows) and "Reset-pass123!" not in str(activity_rows)
    assert "pbkdf2_sha256$" not in str(activity_rows)


async def test_credential_reset_validates_username_and_csrf_before_changing_state(client, bot):
    csrf = await owner_csrf(client, bot)
    await client.post("/dashboard/admin/accounts", data=sub_account_form(csrf), allow_redirects=False)
    account = await bot.accounts.by_username("operator.one")
    raw = await bot.sessions.create(account)
    before = await bot.db.fetchone("SELECT * FROM accounts WHERE id = ?", (account.id,))
    forged = await client.post(f"/dashboard/admin/accounts/{account.id}/credentials", data={
        "csrf": "wrong", "username": "operator.one", "temporary_password": "Reset-pass123!",
    }, headers={"Accept": "application/json"})
    assert forged.status == 403
    duplicate = await client.post(f"/dashboard/admin/accounts/{account.id}/credentials", data={
        "csrf": csrf, "username": " APPARDAS-ADMIN ", "temporary_password": "Reset-pass123!",
    }, headers={"Accept": "application/json"})
    assert duplicate.status == 400 and (await duplicate.json())["field"] == "username"
    missing = await client.post(f"/dashboard/admin/accounts/{account.id}/credentials", data={
        "csrf": csrf, "username": "operator.one",
    }, headers={"Accept": "application/json"})
    assert missing.status == 400 and (await missing.json())["field"] == "temporary_password"
    assert await bot.db.fetchone("SELECT * FROM accounts WHERE id = ?", (account.id,)) == before
    assert await bot.sessions.lookup(raw) is not None
    absent = await client.post("/dashboard/admin/accounts/99999/credentials", data={
        "csrf": csrf, "username": "missing", "temporary_password": "Reset-pass123!",
    }, headers={"Accept": "application/json"})
    assert absent.status == 404


@pytest.mark.parametrize("role", ["viewer", "admin"])
async def test_non_owners_cannot_create_or_reset_credentials(client, bot, role):
    owner = await bot.accounts.by_discord_id(1)
    account = await bot.accounts.create(actor=owner, discord_user_id=SUB_ACCOUNT_ID, username="operator.one", temporary_password=TEMPORARY_PASSWORD, name="Operator One", role=role, guild_ids=[1])
    account = await bot.accounts.change_password(account.id, TEMPORARY_PASSWORD, "Permanent-pass123!")
    raw = await bot.sessions.create(account)
    client.session.cookie_jar.update_cookies({bot.sessions.cookie_name: raw}, client.make_url("/"))
    page = await client.get("/dashboard/g/1")
    csrf = csrf_from(await page.text())
    before = await bot.db.fetchone("SELECT * FROM accounts WHERE id = ?", (account.id,))
    created = await client.post("/dashboard/admin/accounts", data=sub_account_form(csrf), headers={"Accept": "application/json"})
    assert created.status == 404
    reset = await client.post(f"/dashboard/admin/accounts/{account.id}/credentials", data={
        "csrf": csrf, "username": "operator.one", "temporary_password": "Reset-pass123!",
    }, headers={"Accept": "application/json"})
    assert reset.status == 404
    assert await bot.db.fetchone("SELECT * FROM accounts WHERE id = ?", (account.id,)) == before
    assert await bot.sessions.lookup(raw) is not None
    bot.fetch_user.assert_not_awaited()
    bot.fetch_user.return_value.send.assert_not_awaited()


async def test_provisioning_and_credential_reset_roll_back_when_audit_fails(client, bot, monkeypatch, caplog):
    csrf = await owner_csrf(client, bot)

    async def unavailable(**kwargs):
        raise sqlite3.OperationalError("activity unavailable")

    monkeypatch.setattr("app.web.accounts.record_activity", unavailable)
    failed_create = await client.post("/dashboard/admin/accounts", data=sub_account_form(csrf), headers={"Accept": "application/json"})
    assert failed_create.status == 503
    assert await bot.accounts.by_discord_id(SUB_ACCOUNT_ID) is None
    assert await bot.db.scalar("SELECT COUNT(*) FROM account_guilds") == 0
    owner = await bot.accounts.by_discord_id(1)
    account = await bot.accounts.create(actor=owner, discord_user_id=SUB_ACCOUNT_ID, username="operator.one", temporary_password=TEMPORARY_PASSWORD, name="Operator One", role="viewer", guild_ids=[1])
    raw = await bot.sessions.create(account)
    before = await bot.db.fetchone("SELECT * FROM accounts WHERE id = ?", (account.id,))
    failed_reset = await client.post(f"/dashboard/admin/accounts/{account.id}/credentials", data={
        "csrf": csrf, "username": "operator.new", "temporary_password": "Reset-pass123!",
    }, headers={"Accept": "application/json"})
    assert failed_reset.status == 503
    assert await bot.db.fetchone("SELECT * FROM accounts WHERE id = ?", (account.id,)) == before
    assert await bot.sessions.lookup(raw) is not None
    assert TEMPORARY_PASSWORD not in caplog.text and "Reset-pass123!" not in caplog.text
    bot.fetch_user.assert_not_awaited()
    bot.fetch_user.return_value.send.assert_not_awaited()


async def test_discord_oauth_cannot_be_used_even_with_legacy_configuration(client, bot):
    bot.cfg = replace(bot.cfg, discord_client_id="123456789012345678", discord_client_secret="test-only")
    response = await client.get("/login?start=1", allow_redirects=False)
    assert response.status == 200 and "Location" not in response.headers
    assert 'name="username"' in await response.text()
    assert "oauth_client" not in client.server.app
    assert (await client.get("/auth/callback?state=" + "a" * 43 + "&code=test-only", allow_redirects=False)).status == 404
    assert await bot.db.scalar("SELECT COUNT(*) FROM oauth_states") == 0


async def test_create_account_dms_new_discord_id_only_after_creation_commits(client, bot, caplog):
    csrf = await owner_csrf(client, bot)
    recipient = bot.fetch_user.return_value

    async def sent(content, **kwargs):
        assert not bot.db.conn.in_transaction
        account = await bot.accounts.by_discord_id(SUB_ACCOUNT_ID)
        assert account.username == "operator.one" and account.must_change_password
        assert await bot.db.fetchone("SELECT id FROM activity_log WHERE action = 'account.create' AND target = ?", (str(account.id),)) is not None
        assert f"Username: `operator.one`\nTemporary password:\n```\n{TEMPORARY_PASSWORD}\n```" in content
        assert f"{bot.cfg.public_base_url}/login" in content and "first sign-in" in content
        assert kwargs["allowed_mentions"].to_dict()["parse"] == []
        assert kwargs["suppress_embeds"] is True and "file" not in kwargs

    recipient.send.side_effect = sent
    response = await client.post("/dashboard/admin/accounts", data=sub_account_form(csrf), headers={"Accept": "application/json"})
    assert response.status == 200
    payload = await response.json()
    assert payload["dm_delivered"] is True and "dm=sent" in payload["redirect"]
    assert TEMPORARY_PASSWORD not in json.dumps(payload)
    bot.fetch_user.assert_awaited_once_with(int(SUB_ACCOUNT_ID))
    assert int(SUB_ACCOUNT_ID) != (await bot.accounts.by_discord_id(1)).discord_user_id
    recipient.send.assert_awaited_once()
    detail = await client.get(payload["redirect"])
    assert "credentials by DM" in await detail.text()
    duplicate = await client.post("/dashboard/admin/accounts", data=sub_account_form(csrf), headers={"Accept": "application/json"})
    assert duplicate.status == 400
    recipient.send.assert_awaited_once()
    bot.fetch_user.assert_awaited_once_with(int(SUB_ACCOUNT_ID))
    assert TEMPORARY_PASSWORD not in caplog.text
    assert await bot.db.scalar("SELECT COUNT(*) FROM nudges") == 0


async def test_create_account_uses_cached_new_recipient_and_never_dms_admin(client, bot, monkeypatch):
    csrf = await owner_csrf(client, bot)
    recipient = bot.fetch_user.return_value
    admin = SimpleNamespace(id=1, name="administrator", send=AsyncMock())
    lookup = Mock(side_effect=lambda user_id: recipient if user_id == int(SUB_ACCOUNT_ID) else admin if user_id == 1 else None)
    monkeypatch.setattr(bot, "get_user", lookup)
    response = await client.post("/dashboard/admin/accounts", data=sub_account_form(csrf), allow_redirects=False)
    assert response.status == 303 and "dm=sent" in response.headers["Location"]
    lookup.assert_called_once_with(int(SUB_ACCOUNT_ID))
    bot.fetch_user.assert_not_awaited()
    recipient.send.assert_awaited_once()
    admin.send.assert_not_awaited()


@pytest.mark.parametrize("failure", ["forbidden", "http", "not_found", "offline"])
async def test_dm_failure_preserves_account_and_warns_without_leaking_password(client, bot, caplog, failure):
    csrf = await owner_csrf(client, bot)
    recipient = bot.fetch_user.return_value
    if failure == "forbidden":
        recipient.send.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), TEMPORARY_PASSWORD)
    elif failure == "http":
        recipient.send.side_effect = discord.HTTPException(SimpleNamespace(status=503, reason="Unavailable"), TEMPORARY_PASSWORD)
    elif failure == "not_found":
        bot.fetch_user.side_effect = discord.NotFound(SimpleNamespace(status=404, reason="Not Found"), TEMPORARY_PASSWORD)
    else:
        bot.fetch_user.side_effect = RuntimeError(TEMPORARY_PASSWORD)
    response = await client.post("/dashboard/admin/accounts", data=sub_account_form(csrf), headers={"Accept": "application/json"})
    assert response.status == 200
    payload = await response.json()
    assert payload["dm_delivered"] is False and "dm=failed" in payload["redirect"]
    assert "Account created" in payload["message"] and "could not be confirmed" in payload["message"]
    account = await bot.accounts.authenticate("operator.one", TEMPORARY_PASSWORD)
    assert account is not None and account.must_change_password
    assert account.discord_user_id == int(SUB_ACCOUNT_ID)
    assert [row["id"] for row in await bot.accounts.guilds(account.id)] == [1]
    assert await bot.db.scalar("SELECT COUNT(*) FROM activity_log WHERE action = 'account.create'") == 1
    assert TEMPORARY_PASSWORD not in json.dumps(payload) and TEMPORARY_PASSWORD not in caplog.text
    detail = await client.get(payload["redirect"])
    html = await detail.text()
    assert "could not be confirmed" in html and TEMPORARY_PASSWORD not in html
    if failure in ("not_found", "offline"):
        recipient.send.assert_not_awaited()


async def test_dm_timeout_cancels_delivery_and_keeps_committed_account(client, bot, monkeypatch, caplog):
    csrf = await owner_csrf(client, bot)
    cancelled = asyncio.Event()

    async def blocked(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    bot.fetch_user.return_value.send.side_effect = blocked
    monkeypatch.setattr("app.web.accounts.CREDENTIAL_DM_TIMEOUT_S", .01)
    response = await client.post("/dashboard/admin/accounts", data=sub_account_form(csrf), headers={"Accept": "application/json"})
    assert response.status == 200 and (await response.json())["dm_delivered"] is False
    assert cancelled.is_set()
    assert await bot.accounts.authenticate("operator.one", TEMPORARY_PASSWORD) is not None
    assert TEMPORARY_PASSWORD not in caplog.text


@pytest.mark.parametrize("password", ["Temp```pass123!", "  spaced-pass123!  ", "line-one\nline-two", "x" * 2000, "🦊" * 1000])
async def test_dm_attachment_preserves_password_bytes_and_closes_memory_file(client, bot, password):
    csrf = await owner_csrf(client, bot)
    observed = {}

    async def sent(content, **kwargs):
        file = kwargs["file"]
        observed["file"] = file
        observed["password"] = file.fp.read()
        assert file.filename == "temporary-password.txt"
        assert "Username: `operator.one`" in content and "attached text file" in content
        assert password not in content
        assert kwargs["allowed_mentions"].to_dict()["parse"] == []

    bot.fetch_user.return_value.send.side_effect = sent
    form = sub_account_form(csrf)
    form["temporary_password"] = password
    response = await client.post("/dashboard/admin/accounts", data=form, headers={"Accept": "application/json"})
    assert response.status == 200 and (await response.json())["dm_delivered"] is True
    assert observed["password"] == password.encode("utf-8") and observed["file"].fp.closed
    assert await bot.accounts.authenticate("operator.one", password) is not None


async def test_invalid_csrf_does_not_trigger_credentials_dm(client, bot):
    await owner_csrf(client, bot)
    response = await client.post("/dashboard/admin/accounts", data=sub_account_form("wrong"), headers={"Accept": "application/json"})
    assert response.status == 403
    assert await bot.accounts.by_discord_id(SUB_ACCOUNT_ID) is None
    bot.fetch_user.assert_not_awaited()
    bot.fetch_user.return_value.send.assert_not_awaited()


async def test_credential_reset_does_not_send_an_unrequested_dm(client, bot):
    csrf = await owner_csrf(client, bot)
    owner = await bot.accounts.by_discord_id(1)
    account = await bot.accounts.create(actor=owner, discord_user_id=SUB_ACCOUNT_ID, username="operator.one", temporary_password=TEMPORARY_PASSWORD, name="Operator One", role="viewer")
    response = await client.post(f"/dashboard/admin/accounts/{account.id}/credentials", data={
        "csrf": csrf, "username": "operator.one", "temporary_password": "Reset-pass123!",
    }, headers={"Accept": "application/json"})
    assert response.status == 200
    bot.fetch_user.assert_not_awaited()
    bot.fetch_user.return_value.send.assert_not_awaited()


async def test_expired_request_deadline_skips_dm_without_undoing_creation(client, bot, monkeypatch):
    from app.web import accounts as routes

    csrf = await owner_csrf(client, bot)
    deliver = routes._send_credentials_dm

    async def expired(request, account, password):
        request["request_deadline"] = asyncio.get_running_loop().time() - 1
        return await deliver(request, account, password)

    monkeypatch.setattr(routes, "_send_credentials_dm", expired)
    response = await client.post("/dashboard/admin/accounts", data=sub_account_form(csrf), headers={"Accept": "application/json"})
    assert response.status == 200 and (await response.json())["dm_delivered"] is False
    assert await bot.accounts.authenticate("operator.one", TEMPORARY_PASSWORD) is not None
    bot.fetch_user.assert_not_awaited()
    bot.fetch_user.return_value.send.assert_not_awaited()


async def test_failed_attachment_delivery_closes_password_buffer(client, bot, caplog):
    csrf = await owner_csrf(client, bot)
    password = "Private```temp123!"
    observed = {}

    async def denied(content, **kwargs):
        observed["file"] = kwargs["file"]
        raise discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), password)

    bot.fetch_user.return_value.send.side_effect = denied
    form = sub_account_form(csrf)
    form["temporary_password"] = password
    response = await client.post("/dashboard/admin/accounts", data=form, headers={"Accept": "application/json"})
    assert response.status == 200 and (await response.json())["dm_delivered"] is False
    assert observed["file"].fp.closed and password not in caplog.text
    assert await bot.accounts.authenticate("operator.one", password) is not None
