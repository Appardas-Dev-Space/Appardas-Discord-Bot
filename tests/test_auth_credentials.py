from __future__ import annotations

import re
from types import SimpleNamespace

import pytest
from aiohttp.test_utils import TestClient, TestServer

from app.main import AppBot
from app.services.accounts import hash_password, verify_password
from app.web.status import create_app


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
