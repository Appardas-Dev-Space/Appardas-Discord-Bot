"""Request context, fresh authorization, and the shared atomic mutation boundary."""

from __future__ import annotations

import hmac
import logging
import sqlite3
from typing import Any, Awaitable, Callable
from urllib.parse import urlencode

import discord
from aiohttp import web

from ..services.activity import record_activity
from .sessions import Session, safe_next

log = logging.getLogger(__name__)


class WebError(Exception):
    def __init__(self, status: int, error: str, message: str, **data: Any) -> None:
        super().__init__(message)
        self.status, self.error, self.message, self.data = status, error, message, data


def wants_json(request: web.Request) -> bool:
    return request.path in ("/status", "/health", "/dashboard/status.json", "/dashboard/events") or "application/json" in request.headers.get("Accept", "")


def html(body: str, status: int = 200) -> web.Response:
    return web.Response(text=body, status=status, content_type="text/html", headers={"Cache-Control": "no-store"})


def positive_id(value: object) -> int:
    text = str(value)
    if len(text) > 20 or not text.isascii() or not text.isdigit() or not 0 < int(text) <= 2**63 - 1:
        raise WebError(404, "not_found", "That page was not found.")
    return int(text)


async def form_data(request: web.Request) -> Any:
    if "form" not in request:
        if request.content_type not in ("application/x-www-form-urlencoded", "multipart/form-data"):
            raise WebError(400, "bad_request", "Submit a form with valid field values.")
        request["form"] = await request.post()
        if any(not isinstance(value, str) for value in request["form"].values()):
            raise WebError(400, "bad_request", "File uploads are not accepted.")
    return request["form"]


async def log_denial(request: web.Request, *, conn: sqlite3.Connection | None = None, outcome: str = "denied", **kwargs: Any) -> None:
    """Denials stand even when the log cannot be written; never admit on failure."""
    try:
        await record_activity(request=request, outcome=outcome, conn=conn, **kwargs)
    except Exception:
        log.exception("could not record denied request %s", request["request_id"])


async def authorize(request: web.Request, *, conn: sqlite3.Connection | None = None, write: bool = False) -> Session:
    bot = request.app["bot"]
    if conn is None:
        failure = None
        async with bot.db.transaction() as transaction:
            try:
                session = await authorize(request, conn=transaction, write=write)
            except WebError as exc:
                failure = exc
        if failure:
            raise failure
        return session
    raw = request.cookies.get(bot.sessions.cookie_name, "")
    session, account, reason = await bot.sessions.resolve(raw, conn=conn)
    request["session"] = session
    request["account"] = account
    if session is None:
        request["clear_session"] = True
        if reason:
            await log_denial(request, conn=conn, account=account, action="auth.session.expired", detail={"reason": reason, "path": request.path})
        raise WebError(401, "auth", "Sign in to continue.")
    if account.must_change_password and request.path not in ("/change-password", "/logout"):
        if wants_json(request):
            raise WebError(403, "password_change_required", "Password change required before accessing the console.")
        raise web.HTTPFound("/change-password")
    if request.path.startswith("/dashboard/admin/") and account.role != "owner":
        await log_denial(request, conn=conn, action="authz.denied", detail={"reason": "owner_required", "path": request.path})
        raise WebError(404, "not_found", "That page was not found.")
    raw_guild = request.match_info.get("guild_id")
    if raw_guild is not None:
        guild_id = positive_id(raw_guild)
        granted = await bot.accounts.has_grant(account, guild_id, conn=conn)
        guild = bot.get_guild(guild_id)
        if guild is None or not granted:
            await log_denial(request, conn=conn, action="authz.denied", guild_id=guild_id, detail={"reason": "guild_access", "path": request.path})
            raise WebError(404, "not_found", "That page was not found.")
        request["guild"] = guild
        if write and not account.can_write:
            await log_denial(request, conn=conn, action="authz.denied", guild_id=guild_id, detail={"reason": "viewer_write", "path": request.path})
            raise WebError(403, "forbidden", "Your account can view this server but not change it.", needed="admin")
        if request.method == "GET":
            await bot.sessions.remember_guild(session, guild_id, conn=conn)
    if write:
        form = await form_data(request)
        csrf = request.headers.get("X-CSRF-Token") or form.get("csrf", "")
        if not isinstance(csrf, str) or not hmac.compare_digest(session.csrf.encode(), csrf.encode()):
            await log_denial(request, conn=conn, action="authz.denied", guild_id=int(raw_guild) if raw_guild else None, detail={"reason": "csrf", "path": request.path})
            raise WebError(403, "csrf", "The form could not be verified. Reload the page and try again.")
    return session


async def mutation(request: web.Request, operation: Callable[[sqlite3.Connection], Awaitable[Any]]) -> Any:
    """Recheck privileges under the writer lock, then log and change atomically.

    An authorization denial commits only session invalidation and its denial log.
    Exceptions from the operation roll the entire change back.
    """
    await form_data(request)
    failure = None
    result = None
    previous = request.get("session")
    try:
        async with request.app["bot"].db.transaction() as conn:
            try:
                await authorize(request, conn=conn, write=True)
            except WebError as exc:
                failure = exc
            else:
                result = await operation(conn)
    except BaseException:
        # A rolled-back touch must not extend the browser cookie beyond the row.
        request["session"] = previous
        raise
    if failure is not None:
        raise failure
    return result


async def accessible_guilds(request: web.Request) -> list[dict[str, Any]]:
    bot, account = request.app["bot"], request["account"]
    if account.role == "owner":
        guilds = [{"id": guild.id, "name": guild.name} for guild in bot.guilds]
    else:
        guilds = [{"id": row["id"], "name": bot.get_guild(row["id"]).name} for row in await bot.accounts.guilds(account.id) if bot.get_guild(row["id"]) is not None]
    return sorted(guilds, key=lambda guild: (guild["name"].casefold(), guild["id"]))


def login_redirect(request: web.Request) -> web.Response:
    # Query strings may contain legacy credentials; preserve only the route.
    return web.HTTPFound("/login?" + urlencode({"next": safe_next(request.path)}))


def saved(request: web.Request, location: str, payload: dict[str, Any]) -> web.Response:
    if wants_json(request):
        return web.json_response(payload)
    return web.HTTPSeeOther(location)


def invite_url(bot: Any) -> str | None:
    return None
