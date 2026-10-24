"""Public routes: one running tree, one process, no generated site copy."""

from __future__ import annotations

import hashlib
from typing import Any

from aiohttp import web

from .common import WebError, invite_url
from .templates.base import e
from .templates.public.commands import commands_page
from .templates.public.getting_started import getting_started_page
from .templates.public.landing import landing_page
from .templates.public.layout import layout

REVALIDATE = "public, max-age=0, must-revalidate"


def cached(request: web.Request, body: str | bytes, *, version: str, content_type: str, cache: str = REVALIDATE, status: int = 200) -> web.Response:
    etag = '"' + hashlib.sha256(version.encode()).hexdigest()[:24] + '"'
    headers = {"Cache-Control": cache, "ETag": etag}
    matches = [tag.strip().removeprefix("W/") for tag in request.headers.get("If-None-Match", "").split(",")]
    if etag in matches or "*" in matches:
        return web.Response(status=304, headers=headers)
    return web.Response(body=body.encode() if isinstance(body, str) else body, content_type=content_type, headers=headers, status=status)


async def get_request_account(request: web.Request) -> Any | None:
    bot = request.app["bot"]
    cookie = request.cookies.get(bot.sessions.cookie_name)
    if not cookie:
        return None
    session, account, _ = await bot.sessions.resolve(cookie, touch=False)
    return account if session and account and account.status == "active" else None


def public_html(request: web.Request, content: str, *, title: str, description: str, version: str, close: bool = False, account: Any | None = None) -> web.Response:
    bot = request.app["bot"]
    body = layout(title=title, description=description, content=content, base_url=bot.cfg.public_base_url, path=request.path, invite=invite_url(bot), close=close, account=account)
    cache = "private, no-cache" if account else REVALIDATE
    account_tag = f":acc:{account.id}" if account else ""
    # The query is part of the representation (search), not of the canonical URL.
    return cached(request, body, version=version + request.path + request.query.get("q", "")[:200] + account_tag, content_type="text/html", cache=cache)


async def landing(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    account = await get_request_account(request)
    snapshot = await bot.status_service.public_snapshot()
    return public_html(request, landing_page(snapshot, bot.manifest, invite_url(bot), account=account), title="A Discord bot, with a record", description="Five cogs in one process: GitHub watches, announcements, nudges, settings and live status.", version=snapshot["checked_at"] + bot.manifest["version"], close=True, account=account)


async def commands(request: web.Request) -> web.Response:
    manifest = request.app["bot"].manifest
    cog = request.match_info.get("cog")
    if cog is not None and cog not in manifest["cogs"]:
        raise WebError(404, "not_found", "That cog is not loaded. Choose one of the five cogs below.")
    account = await get_request_account(request)
    return public_html(request, commands_page(manifest, cog=cog, query=request.query.get("q", "")[:200].strip()), title=f"{cog} commands" if cog else "Commands", description="Every loaded command, with usage, permissions, examples and failure notes.", version=manifest["version"], account=account)


async def getting_started(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    account = await get_request_account(request)
    return public_html(request, getting_started_page(invite_url(bot)), title="Getting started", description="Invite appardas, choose a channel, and make your first post.", version=bot.manifest["version"], account=account)


async def robots(request: web.Request) -> web.Response:
    body = "User-agent: *\nAllow: /\nDisallow: /dashboard\nDisallow: /login\nDisallow: /auth\nDisallow: /status\nSitemap: " + request.app["bot"].cfg.public_base_url + "/sitemap.xml\n"
    return cached(request, body, version=body, content_type="text/plain")


async def sitemap(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    paths = ["/", "/commands", *[f"/commands/{key}" for key in bot.manifest["cogs"]], "/getting-started"]
    body = '<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + ''.join(f'<url><loc>{e(bot.cfg.public_base_url + path)}</loc></url>' for path in paths) + '</urlset>'
    return cached(request, body, version=body, content_type="application/xml")


def setup_routes(app: web.Application) -> None:
    app.router.add_get("/", landing)
    app.router.add_get("/commands", commands)
    app.router.add_get("/commands/{cog}", commands)
    app.router.add_get("/getting-started", getting_started)
    app.router.add_get("/robots.txt", robots)
    app.router.add_get("/sitemap.xml", sitemap)
