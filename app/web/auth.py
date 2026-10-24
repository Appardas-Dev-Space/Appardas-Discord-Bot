"""Operator credential sign-in, password management, and Discord OAuth."""

from __future__ import annotations

import asyncio
import hmac
import logging
import sqlite3
from typing import Any
from urllib.parse import urlencode

from aiohttp import web

from ..services.accounts import AccountError
from ..services.activity import record_activity, remote_ip
from ..util import iso
from .common import authorize, form_data, html, invite_url, log_denial, mutation, saved
from .sessions import OAUTH_COOKIE, OPAQUE_ID, safe_next
from .templates.public.change_password import change_password_page
from .templates.public.layout import layout
from .templates.public.login import login_page

log = logging.getLogger(__name__)


def login_response(request: web.Request, message: str = "", *, status: int = 200, next_path: str = "/dashboard") -> web.Response:
    bot = request.app["bot"]
    configured = bool(bot.cfg.discord_client_id and bot.cfg.discord_client_secret)
    return html(
        layout(
            title="Sign in",
            description="Accounts are created by the bot owner. Enter your operator credentials to access the console.",
            content=login_page(next_path=next_path, message=message, configured=configured, invite=invite_url(bot)),
            base_url=bot.cfg.public_base_url,
            path="/login",
            private=True,
        ),
        status,
    )


def change_password_response(request: web.Request, message: str = "", *, status: int = 200, next_path: str = "/dashboard") -> web.Response:
    bot = request.app["bot"]
    session = request["session"]
    return html(
        layout(
            title="Change password",
            description="Change your operator password.",
            content=change_password_page(csrf=session.csrf, message=message, next_path=next_path),
            base_url=bot.cfg.public_base_url,
            path="/change-password",
            private=True,
        ),
        status,
    )


async def login(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    next_path = safe_next(request.query.get("next", "/dashboard"))
    session, account, reason = await bot.sessions.resolve(request.cookies.get(bot.sessions.cookie_name, ""))
    request["session"], request["account"] = session, account
    if session:
        if account and account.must_change_password:
            return web.HTTPFound("/change-password")
        return web.HTTPFound(next_path)
    if reason:
        request["clear_session"] = True
        await log_denial(request, action="auth.session.expired", account=account, detail={"reason": reason, "path": request.path})
    if request.query.get("start") == "1" and bot.cfg.discord_client_id and bot.cfg.discord_client_secret:
        state = await bot.sessions.create_state(next_path)
        query = urlencode({"client_id": bot.cfg.discord_client_id, "response_type": "code", "redirect_uri": bot.cfg.public_base_url + "/auth/callback", "scope": "identify", "state": state})
        response = web.HTTPFound("https://discord.com/oauth2/authorize?" + query)
        bot.sessions.set_oauth_cookie(response, state)
        return response
    return login_response(request, "Signed out." if request.query.get("signed_out") == "1" else "", next_path=next_path)


async def login_post(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    form = await form_data(request)
    username = (form.get("username") or "").strip()
    password = form.get("password") or ""
    next_path = safe_next(form.get("next") or request.query.get("next", "/dashboard"))

    if not username or not password:
        return login_response(request, "Enter your username and password.", status=400, next_path=next_path)

    account = await bot.accounts.authenticate(username, password, ip=remote_ip(request, bot.cfg.trust_proxy))
    if account is None:
        await log_denial(request, action="auth.login.invalid_credentials", detail={"username": username})
        return login_response(request, "Invalid username or password.", status=401, next_path=next_path)
    if account.status != "active":
        await log_denial(request, action="auth.login.denied_inactive", account=account)
        return login_response(request, "This account is not active. Ask the administrator.", status=403, next_path=next_path)

    async with bot.db.transaction() as conn:
        raw = await bot.sessions.create(account, username=account.username or account.name, conn=conn)
        await bot.sessions.delete(request.cookies.get(bot.sessions.cookie_name, ""), conn=conn)
        await record_activity(request=request, action="auth.login.success", account=account, conn=conn)

    target = "/change-password" if account.must_change_password else next_path
    response = web.HTTPFound(target)
    bot.sessions.set_cookie(response, raw)
    return response


async def change_password(request: web.Request) -> web.Response:
    session = await authorize(request)
    next_path = safe_next(request.query.get("next", "/dashboard"))
    return change_password_response(request, next_path=next_path)


async def change_password_post(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    form = await form_data(request)
    current_password = form.get("current_password") or ""
    new_password = form.get("new_password") or ""
    confirm_password = form.get("confirm_password") or ""
    next_path = safe_next(form.get("next") or request.query.get("next", "/dashboard"))

    if not current_password or not new_password or not confirm_password:
        return change_password_response(request, "All password fields are required.", status=400, next_path=next_path)
    if new_password != confirm_password:
        return change_password_response(request, "New password and confirmation do not match.", status=400, next_path=next_path)
    if new_password == "admin":
        return change_password_response(request, "New password cannot be the default password.", status=400, next_path=next_path)
    if len(new_password) < 6:
        return change_password_response(request, "New password must be at least 6 characters.", status=400, next_path=next_path)
    if new_password == current_password:
        return change_password_response(request, "New password must be different from current password.", status=400, next_path=next_path)

    account = request["account"]
    try:
        async def operation(conn: sqlite3.Connection):
            updated = await bot.accounts.change_password(account.id, current_password, new_password, conn=conn)
            await record_activity(request=request, action="account.password_change", account=updated, conn=conn)
            return updated
        await mutation(request, operation)
    except AccountError as exc:
        return change_password_response(request, str(exc), status=400, next_path=next_path)

    return saved(request, next_path, {"ok": True})


async def exchange_identity(request: web.Request, code: str) -> dict[str, Any]:
    config, client = request.app["bot"].cfg, request.app["oauth_client"]
    async with client.post("https://discord.com/api/oauth2/token", data={"client_id": str(config.discord_client_id), "client_secret": config.discord_client_secret, "grant_type": "authorization_code", "code": code, "redirect_uri": config.public_base_url + "/auth/callback"}) as response:
        if response.status != 200:
            raise ValueError("token exchange")
        token = await response.json()
    if not isinstance(token, dict) or not isinstance(token.get("access_token"), str) or not token["access_token"]:
        raise ValueError("token payload")
    async with client.get("https://discord.com/api/users/@me", headers={"Authorization": "Bearer " + token["access_token"]}) as response:
        if response.status != 200:
            raise ValueError("identity request")
        identity = await response.json()
    if not isinstance(identity, dict):
        raise ValueError("identity payload")
    user_id = str(identity.get("id", ""))
    if len(user_id) > 20 or not user_id.isascii() or not user_id.isdigit() or not 0 < int(user_id) <= 2**63 - 1:
        raise ValueError("identity ID")
    if not isinstance(identity.get("username"), str):
        raise ValueError("identity name")
    return identity


async def callback(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    request["clear_oauth"] = True
    state, cookie = request.query.get("state", ""), request.cookies.get(OAUTH_COOKIE, "")
    next_path = None
    if OPAQUE_ID.fullmatch(state) and OPAQUE_ID.fullmatch(cookie) and hmac.compare_digest(state, cookie):
        next_path = await bot.sessions.consume_state(state)
    if next_path is None:
        await log_denial(request, action="auth.login.state_mismatch", detail={"reason": "invalid_state"})
        return login_response(request, "That sign-in took too long. Try again.", status=403)
    try:
        code = request.query.get("code", "")
        if not code or len(code) > 2048 or "error" in request.query:
            raise ValueError("missing code")
        identity = await asyncio.wait_for(exchange_identity(request, code), timeout=8)
    except Exception:
        await log_denial(request, action="auth.login.error", outcome="error", detail={"reason": "oauth_exchange"})
        return login_response(request, "Discord did not complete the sign-in. Try again.", status=503, next_path=next_path)
    request["discord_user_id"] = int(identity["id"])
    denied = None
    try:
        async with bot.db.transaction() as conn:
            account = await bot.accounts.by_discord_id(int(identity["id"]), conn=conn)
            request["account"] = account
            if account is None or account.status != "active":
                denied = "unknown" if account is None else account.status
                await log_denial(request, action="auth.login.denied_" + denied, account=account, detail={"reason": "no_account" if account is None else "account_inactive"}, conn=conn)
            else:
                raw = await bot.sessions.create(account, username=identity["username"], avatar=identity.get("avatar") if isinstance(identity.get("avatar"), str) else None, conn=conn)
                await bot.sessions.delete(request.cookies.get(bot.sessions.cookie_name, ""), conn=conn)
                await bot.db.execute_on(conn, "UPDATE accounts SET last_login_at = ?, last_login_ip = ? WHERE id = ?", (iso(), remote_ip(request, bot.cfg.trust_proxy), account.id))
                await record_activity(request=request, action="auth.login.success", account=account, conn=conn)
    except Exception:
        log.exception("sign-in transaction failed request=%s", request["request_id"])
        return login_response(request, "Could not complete sign-in. Try again.", status=503, next_path=next_path)
    if denied:
        return login_response(request, "This Discord account has not been invited. Ask the owner for access." if denied == "unknown" else "This account is not active. Ask the owner.", status=403, next_path=next_path)
    response = web.HTTPFound(next_path)
    bot.sessions.set_cookie(response, raw)
    return response


async def logout(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    async def operation(conn):
        await bot.sessions.delete(request.cookies.get(bot.sessions.cookie_name, ""), conn=conn)
        await record_activity(request=request, action="auth.logout", conn=conn)
    await mutation(request, operation)
    request["clear_session"] = True
    return web.HTTPSeeOther("/login?signed_out=1")


def setup_routes(app: web.Application) -> None:
    app.router.add_get("/login", login)
    app.router.add_post("/login", login_post)
    app.router.add_get("/change-password", change_password)
    app.router.add_post("/change-password", change_password_post)
    app.router.add_get("/auth/callback", callback)
    app.router.add_post("/logout", logout)
