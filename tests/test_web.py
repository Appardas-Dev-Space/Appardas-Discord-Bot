from __future__ import annotations

import asyncio
import gzip
import re
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

from app.main import AppBot
from app.web.asset_store import CSS_NAME, LOGO_NAME
from app.web.status import create_app


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', html)
    assert match, "page did not contain a CSRF field"
    return match.group(1)


async def sign_in(client: TestClient, bot: AppBot, raw: str) -> None:
    client.session.cookie_jar.update_cookies({bot.sessions.cookie_name: raw}, client.make_url("/"))


@pytest.fixture
async def bot(cfg):
    instance = AppBot(cfg)
    await instance.prepare()
    await instance.settings_service.ensure_guild(1, "Test Guild")
    instance._connection._guilds[1] = SimpleNamespace(
        id=1,
        name="Test Guild",
        text_channels=[SimpleNamespace(id=10, name="general")],
        roles=[SimpleNamespace(id=1, name="@everyone", position=0)],
    )
    yield instance
    await instance.close()
    await instance.shutdown()


@pytest.fixture
async def client(bot):
    instance = TestClient(TestServer(create_app(bot)))
    await instance.start_server()
    yield instance
    await instance.close()


async def owner_session(bot: AppBot) -> tuple[object, str]:
    account = await bot.accounts.by_discord_id(1)
    assert account is not None
    return account, await bot.sessions.create(account, username="owner")


async def viewer_session(bot: AppBot, *, granted: bool = True) -> tuple[object, str]:
    owner = await bot.accounts.by_discord_id(1)
    viewer = await bot.accounts.create(
        actor=owner,
        discord_user_id=123456789012345678,
        name="Viewer One",
        role="viewer",
        guild_ids=[1] if granted else [],
    )
    return viewer, await bot.sessions.create(viewer, username="viewer")


async def test_public_surface_assets_gzip_and_etags(client):
    landing = await client.get("/")
    assert landing.status == 200
    body = await landing.text()
    assert "appardas" in body
    assert f'<img class="brand-logo" src="/assets/{LOGO_NAME}"' in body
    assert 'href="/login">Sign in</a>' in body
    assert 'Console sign in</a>' in body
    assert landing.headers["X-Content-Type-Options"] == "nosniff"
    assert "frame-ancestors 'none'" in landing.headers["Content-Security-Policy"]
    assert landing.headers["Cache-Control"].startswith("public")

    commands = await client.get("/commands")
    assert commands.status == 200
    assert "Commands" in await commands.text()
    assert (await client.get("/commands/nope")).status == 404
    assert (await client.get("/getting-started")).status == 200
    assert (await client.get("/register")).status == 404
    assert (await client.get("/robots.txt")).status == 200
    assert (await client.get("/sitemap.xml")).status == 200

    health = await client.get("/health")
    assert health.status == 200
    assert (await health.json())["status"] == "ok"
    status = await client.get("/status")
    assert status.status in (200, 503)
    assert (await status.json())["status"] in ("ok", "degraded", "offline")

    asset = await client.get(f"/assets/{CSS_NAME}")
    assert asset.status == 200
    assert asset.headers["Cache-Control"].startswith("public")
    etag = asset.headers["ETag"]
    conditional = await client.get(f"/assets/{CSS_NAME}", headers={"If-None-Match": etag})
    assert conditional.status == 304

    logo_asset = await client.get(f"/assets/{LOGO_NAME}")
    assert logo_asset.status == 200
    assert logo_asset.headers["Content-Type"] == "image/png"
    assert logo_asset.headers["Cache-Control"].startswith("public")

    # The ETag is computed before compression, so a revalidating browser gets 304.
    landing_etag = landing.headers["ETag"]
    revalidated = await client.get("/", headers={"If-None-Match": landing_etag, "Accept-Encoding": "gzip"})
    assert revalidated.status == 304

    raw = await client.session.get(client.make_url("/"), headers={"Accept-Encoding": "gzip"}, auto_decompress=False)
    try:
        assert raw.headers.get("Content-Encoding") == "gzip"
        assert raw.headers.get("Vary") == "Accept-Encoding"
        decoded = gzip.decompress(await raw.read()).decode()
        assert "appardas" in decoded
    finally:
        raw.close()


async def test_public_surface_authenticated_session(client, bot):
    account, raw_session = await owner_session(bot)
    await sign_in(client, bot, raw_session)

    landing = await client.get("/")
    assert landing.status == 200
    body = await landing.text()
    assert 'href="/dashboard">Dashboard</a>' in body
    assert 'Open Console</a>' in body
    assert 'href="/login">Sign in</a>' not in body
    assert 'Console sign in</a>' not in body
    assert landing.headers["Cache-Control"].startswith("private")

    commands = await client.get("/commands")
    assert commands.status == 200
    cmd_body = await commands.text()
    assert 'href="/dashboard">Dashboard</a>' in cmd_body
    assert 'href="/login">Sign in</a>' not in cmd_body
    assert commands.headers["Cache-Control"].startswith("private")


async def test_anonymous_requests_redirect_or_return_json(client):
    dashboard = await client.get("/dashboard", allow_redirects=False)
    assert dashboard.status == 302
    assert dashboard.headers["Location"].startswith("/login?next=")
    assert "dashboard" in dashboard.headers["Location"]

    json_dashboard = await client.get("/dashboard", headers={"Accept": "application/json"})
    assert json_dashboard.status == 401
    assert (await json_dashboard.json())["error"] == "auth"

    login = await client.get("/login")
    assert login.status == 200
    text = await login.text()
    assert "Sign in" in text
    assert "Accounts are created by the bot owner" in text
    # Without configured OAuth credentials the start action explains rather than redirecting.
    assert (await client.get("/login?start=1")).status == 200

    callback = await client.get("/auth/callback?state=" + "a" * 43)
    assert callback.status == 403
    assert "took too long" in await callback.text()

    logout = await client.post("/logout", data={"csrf": "x"}, allow_redirects=False)
    assert logout.status == 302
    assert logout.headers["Location"].startswith("/login")


async def test_owner_and_viewer_authorization(client, bot):
    owner, owner_raw = await owner_session(bot)
    viewer, viewer_raw = await viewer_session(bot)

    await sign_in(client, bot, owner_raw)
    root = await client.get("/dashboard", allow_redirects=False)
    assert root.status == 302
    assert root.headers["Location"] == "/dashboard/g/1"
    overview = await client.get("/dashboard/g/1")
    assert overview.status == 200
    assert "Right now" in await overview.text()
    owner_csrf = csrf_from(await overview.text())

    # A viewer may read the granted guild but cannot enter owner screens.
    viewer_client = TestClient(TestServer(create_app(bot)))
    await viewer_client.start_server()
    try:
        await sign_in(viewer_client, bot, viewer_raw)
        viewer_page = await viewer_client.get("/dashboard/g/1")
        assert viewer_page.status == 200
        viewer_csrf = csrf_from(await viewer_page.text())
        assert (await viewer_client.get("/dashboard/admin/accounts")).status == 404
        assert (await viewer_client.get("/dashboard/admin/activity")).status == 404
        assert (await viewer_client.get("/dashboard/g/2")).status == 404
        denied = await viewer_client.post(
            "/dashboard/g/1/cogs/dev",
            data={"csrf": viewer_csrf, "enabled": "0"},
            headers={"Accept": "application/json"},
        )
        assert denied.status == 403
        assert (await denied.json())["error"] == "forbidden"
        denied_setting = await viewer_client.post(
            "/dashboard/g/1/settings/prefix",
            data={"csrf": viewer_csrf, "value": "?"},
            headers={"Accept": "application/json"},
        )
        assert denied_setting.status == 403

        # A revoked grant is rechecked inside the write transaction.
        await bot.accounts.set_grants(viewer.id, set(), actor=owner)
        revoked = await viewer_client.post(
            "/dashboard/g/1/cogs/dev",
            data={"csrf": viewer_csrf, "enabled": "1"},
            headers={"Accept": "application/json"},
        )
        assert revoked.status == 404
    finally:
        await viewer_client.close()

    # The owner's valid CSRF token changes the cog and records both trails.
    toggled = await client.post(
        "/dashboard/g/1/cogs/dev",
        data={"csrf": owner_csrf, "enabled": "0", "reason": "maintenance"},
        headers={"Accept": "application/json"},
    )
    assert toggled.status == 200
    payload = await toggled.json()
    assert payload["enabled"] is False
    state = await bot.settings_service.cog_state(1, "dev")
    assert state.enabled is False
    assert state.reason == "maintenance"
    audit = await bot.db.fetchone("SELECT * FROM audit_log WHERE guild_id = 1 AND action = 'cog.toggle'")
    assert audit is not None
    activity = await bot.db.fetchone("SELECT * FROM activity_log WHERE action = 'cog.toggle'")
    assert activity is not None
    assert activity["account_id"] == owner.id

    forged = await client.post(
        "/dashboard/g/1/cogs/dev",
        data={"csrf": "wrong", "enabled": "1"},
        headers={"Accept": "application/json"},
    )
    assert forged.status == 403
    assert (await bot.settings_service.cog_state(1, "dev")).enabled is False

    setting = await client.post(
        "/dashboard/g/1/settings/prefix",
        data={"csrf": owner_csrf, "value": "!"},
        headers={"Accept": "application/json"},
    )
    assert setting.status == 200
    assert (await setting.json())["value"] == "!"
    assert await bot.settings_service.get_setting(1, "prefix") == "!"

    clear_empty = await client.post(
        "/dashboard/g/1/settings/prefix",
        data={"csrf": owner_csrf, "value": ""},
        headers={"Accept": "application/json"},
    )
    assert clear_empty.status == 200
    assert (await clear_empty.json())["value"] is None
    assert await bot.settings_service.get_setting(1, "prefix") is None

    setting2 = await client.post(
        "/dashboard/g/1/settings/prefix",
        data={"csrf": owner_csrf, "value": "??"},
        headers={"Accept": "application/json"},
    )
    assert setting2.status == 200
    assert await bot.settings_service.get_setting(1, "prefix") == "??"

    clear_keyword = await client.post(
        "/dashboard/g/1/settings/prefix",
        data={"csrf": owner_csrf, "value": "reset"},
        headers={"Accept": "application/json"},
    )
    assert clear_keyword.status == 200
    assert (await clear_keyword.json())["value"] is None
    assert await bot.settings_service.get_setting(1, "prefix") is None

    invalid = await client.post(
        "/dashboard/g/1/settings/timezone",
        data={"csrf": owner_csrf, "value": "Not/AZone"},
        headers={"Accept": "application/json"},
    )
    assert invalid.status == 400
    assert await bot.settings_service.get_setting(1, "timezone") == "UTC"

    denied_row = await bot.db.fetchone("SELECT * FROM activity_log WHERE action = 'authz.denied' AND outcome = 'denied'")
    assert denied_row is not None


async def test_account_lifecycle_and_activity_export(client, bot):
    owner, raw = await owner_session(bot)
    await sign_in(client, bot, raw)
    listing = await client.get("/dashboard/admin/accounts")
    assert listing.status == 200
    assert "Owner 1" in await listing.text()
    csrf = csrf_from(await listing.text())

    created = await client.post(
        "/dashboard/admin/accounts",
        data={
            "csrf": csrf,
            "discord_user_id": "223456789012345678",
            "name": "Operator",
            "role": "viewer",
            "note": "temporary",
            "guilds": "1",
        },
        allow_redirects=False,
    )
    assert created.status == 303
    account = await bot.accounts.by_discord_id(223456789012345678)
    assert account is not None
    assert created.headers["Location"].startswith(f"/dashboard/admin/accounts/{account.id}")

    detail = await client.get(f"/dashboard/admin/accounts/{account.id}")
    assert detail.status == 200
    text = await detail.text()
    assert "Operator" in text
    assert "Viewer One" not in text
    csrf = csrf_from(text)

    duplicate = await client.post(
        "/dashboard/admin/accounts",
        data={"csrf": csrf, "discord_user_id": "223456789012345678", "name": "Again", "role": "viewer"},
        headers={"Accept": "application/json"},
    )
    assert duplicate.status == 400

    updated = await client.post(
        f"/dashboard/admin/accounts/{account.id}",
        data={"csrf": csrf, "name": "Operator Two", "role": "admin", "note": "promoted"},
        headers={"Accept": "application/json"},
    )
    assert updated.status == 200
    account = await bot.accounts.get(account.id)
    assert account.name == "Operator Two"
    assert account.role == "admin"

    revoked = await client.post(
        f"/dashboard/admin/accounts/{account.id}/guilds",
        data={"csrf": csrf},
        headers={"Accept": "application/json"},
    )
    assert revoked.status == 200
    assert (await revoked.json())["removed"] == [1]
    assert await bot.accounts.guilds(account.id) == []

    granted = await client.post(
        f"/dashboard/admin/accounts/{account.id}/guilds",
        data={"csrf": csrf, "guilds": "1"},
        headers={"Accept": "application/json"},
    )
    assert granted.status == 200
    assert (await granted.json())["added"] == [1]

    viewer_raw = await bot.sessions.create(account, username="operator")
    suspended = await client.post(
        f"/dashboard/admin/accounts/{account.id}/status",
        data={"csrf": csrf, "status": "suspended"},
        headers={"Accept": "application/json"},
    )
    assert suspended.status == 200
    assert "1 session deleted" in (await suspended.json())["message"]
    assert await bot.db.fetchone("SELECT id_hash FROM sessions WHERE account_id = ?", (account.id,)) is None

    suspended_client = TestClient(TestServer(create_app(bot)))
    await suspended_client.start_server()
    try:
        await sign_in(suspended_client, bot, viewer_raw)
        response = await suspended_client.get("/dashboard", allow_redirects=False)
        assert response.status == 302
    finally:
        await suspended_client.close()

    reactivated = await client.post(
        f"/dashboard/admin/accounts/{account.id}/status",
        data={"csrf": csrf, "status": "active"},
        headers={"Accept": "application/json"},
    )
    assert reactivated.status == 200

    activity = await client.get("/dashboard/admin/activity")
    assert activity.status == 200
    activity_text = await activity.text()
    assert "account.create" in activity_text
    assert "account.suspend" in activity_text

    csv_response = await client.get("/dashboard/admin/activity.csv")
    assert csv_response.status == 200
    assert csv_response.headers["Content-Type"].startswith("text/csv")
    assert "attachment" in csv_response.headers["Content-Disposition"]
    csv_text = await csv_response.text()
    assert csv_text.startswith("\ufeffid,created_at")
    assert "account.create" in csv_text

    assert (await client.get("/dashboard/admin/accounts/999999")).status == 404


async def test_owner_screens_are_not_available_to_viewers(client, bot):
    _, raw = await viewer_session(bot)
    await sign_in(client, bot, raw)
    for path in ("/dashboard/admin/accounts", "/dashboard/admin/activity", "/dashboard/admin/activity.csv"):
        assert (await client.get(path)).status == 404


async def test_failed_activity_write_rolls_back_the_cog_change(client, bot, monkeypatch):
    _, raw = await owner_session(bot)
    await sign_in(client, bot, raw)
    page = await client.get("/dashboard/g/1/cogs")
    csrf = csrf_from(await page.text())

    async def broken(**kwargs):
        raise RuntimeError("activity insert failed")

    monkeypatch.setattr("app.web.dashboard.record_activity", broken)
    response = await client.post(
        "/dashboard/g/1/cogs/dev",
        data={"csrf": csrf, "enabled": "0"},
        headers={"Accept": "application/json"},
    )
    assert response.status == 503
    assert (await bot.settings_service.cog_state(1, "dev")).enabled is True
    assert await bot.db.fetchone("SELECT id FROM audit_log WHERE guild_id = 1 AND action = 'cog.toggle'") is None


async def test_session_expiry_is_deleted_and_logged(client, bot):
    _, raw = await owner_session(bot)
    await sign_in(client, bot, raw)
    await bot.db.execute("UPDATE sessions SET last_seen_at = '2000-01-01T00:00:00+00:00'")
    response = await client.get("/dashboard", allow_redirects=False)
    assert response.status == 302
    assert await bot.db.fetchone("SELECT id_hash FROM sessions") is None
    expired = await bot.db.fetchone("SELECT * FROM activity_log WHERE action = 'auth.session.expired'")
    assert expired is not None


async def test_sse_cap_is_independent_of_general_connection_slots(client, bot):
    _, raw = await owner_session(bot)
    await sign_in(client, bot, raw)
    app = client.server.app
    slots = app["sse_slots"]
    for _ in range(8):
        await slots.acquire()
    try:
        response = await client.get("/dashboard/events", headers={"Accept": "application/json"})
        assert response.status == 503
        assert (await response.json())["error"] == "busy"
    finally:
        for _ in range(8):
            slots.release()

    general = app["connection_slots"]
    for _ in range(app["bot"].cfg.max_connections):
        await general.acquire()
    try:
        busy = await client.get("/health")
        assert busy.status == 503
    finally:
        for _ in range(app["bot"].cfg.max_connections):
            general.release()


async def test_owner_admin_presence_lifecycle(client, bot):
    # 1. Viewer denied
    _, viewer_raw = await viewer_session(bot)
    await sign_in(client, bot, viewer_raw)
    denied = await client.get("/dashboard/admin/presence")
    assert denied.status in (302, 403, 404)

    # 2. Owner allowed
    _, owner_raw = await owner_session(bot)
    await sign_in(client, bot, owner_raw)
    get_res = await client.get("/dashboard/admin/presence")
    assert get_res.status == 200
    html = await get_res.text()
    assert "Bot Presence" in html
    csrf = csrf_from(html)

    # 3. Post new presence
    post_res = await client.post(
        "/dashboard/admin/presence",
        data={
            "csrf": csrf,
            "text": "⚠️ Outage Detected - Investigating",
            "type": "custom",
            "status": "dnd",
        },
    )
    assert post_res.status in (200, 302, 303)
    presence = await bot.status_service.get_presence()
    assert presence["text"] == "⚠️ Outage Detected - Investigating"
    assert presence["type"] == "custom"
    assert presence["status"] == "dnd"

    # 4. Reset presence
    reset_res = await client.post(
        "/dashboard/admin/presence",
        data={
            "csrf": csrf,
            "action": "reset",
        },
    )
    assert reset_res.status in (200, 302, 303)
    reset_presence = await bot.status_service.get_presence()
    assert reset_presence["text"] == bot.cfg.activity_text
    assert reset_presence["status"] == "online"


async def test_dashboard_announcements_and_commands(client, bot):
    class MockChannel:
        id = 10
        name = "general"
        def __init__(self):
            self.sent_messages = []
        async def send(self, **kwargs):
            self.sent_messages.append(kwargs)
            return SimpleNamespace(jump_url="https://discord.com")

    mock_ch = MockChannel()
    bot.get_channel = lambda cid: mock_ch if cid == 10 else None

    # 1. Sign in as owner
    _, owner_raw = await owner_session(bot)
    await sign_in(client, bot, owner_raw)

    # 2. GET announcements page
    res = await client.get("/dashboard/g/1/announcements")
    assert res.status == 200
    html = await res.text()
    assert "Announcements" in html
    assert "New Announcement" in html
    csrf = csrf_from(html)

    # 3. POST create scheduled announcement
    create_res = await client.post(
        "/dashboard/g/1/announcements",
        data={
            "csrf": csrf,
            "channel_id": "10",
            "title": "Community Update",
            "body": "Welcome to our server!",
            "when": "tomorrow 10:00",
            "action_type": "schedule",
            "footer_text": "Official Staff Notice",
            "fields_text": "Rule 1 | Be respectful\nRule 2 | Have fun",
            "color": "#5865F2",
            "ping_role_id": "everyone",
        },
    )
    assert create_res.status in (200, 302, 303)
    pending = await bot.announcements.list_pending(1)
    assert len(pending) == 1
    announcement_id = pending[0]["id"]

    # 4. POST edit announcement
    edit_res = await client.post(
        f"/dashboard/g/1/announcements/{announcement_id}/edit",
        data={
            "csrf": csrf,
            "title": "Community Update v2",
            "body": "Welcome to our upgraded server!",
            "when": "tomorrow 12:00",
            "channel_id": "10",
            "footer_text": "Server Staff Team",
        },
    )
    assert edit_res.status in (200, 302, 303)
    updated_row = await bot.announcements.get(1, announcement_id)
    assert "Community Update v2" in updated_row["payload"]

    # 5. POST dispatch now
    send_res = await client.post(
        f"/dashboard/g/1/announcements/{announcement_id}/send",
        data={"csrf": csrf},
    )
    assert send_res.status in (200, 302, 303)
    dispatched_row = await bot.announcements.get(1, announcement_id)
    assert dispatched_row["status"] == "sent"
    assert len(mock_ch.sent_messages) == 1

    # 6. POST create and cancel announcement
    create2_res = await client.post(
        "/dashboard/g/1/announcements",
        data={
            "csrf": csrf,
            "channel_id": "10",
            "title": "Temporary Event",
            "body": "Event details",
            "when": "in 5 hours",
            "action_type": "schedule",
        },
    )
    assert create2_res.status in (200, 302, 303)
    pending2 = await bot.announcements.list_pending(1)
    assert len(pending2) == 1
    cancel_id = pending2[0]["id"]

    cancel_res = await client.post(
        f"/dashboard/g/1/announcements/{cancel_id}/cancel",
        data={"csrf": csrf},
    )
    assert cancel_res.status in (200, 302, 303)
    cancelled_row = await bot.announcements.get(1, cancel_id)
    assert cancelled_row["status"] == "cancelled"

    # 7. Record command in command_log and test commands log page
    await bot.command_log.record(
        guild_id=1,
        channel_id=10,
        user_id=999,
        user_name="testuser",
        command="appardas help",
        options={"topic": "announcements"},
        status="success",
    )

    cmd_res = await client.get("/dashboard/g/1/commands")
    assert cmd_res.status == 200
    cmd_html = await cmd_res.text()
    assert "Command Log" in cmd_html
    assert "appardas help" in cmd_html
    assert "testuser" in cmd_html


