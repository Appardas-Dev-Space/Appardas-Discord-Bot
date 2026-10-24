"""One HTTP listener for public documents, cookie auth and the private console."""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import sqlite3
import time
import zlib
from typing import Any

from aiohttp import ClientSession, ClientTimeout, web

from ..services.activity import remote_ip
from .asset_store import ASSETS, FAVICON_BYTES, FAVICON_NAME
from .common import WebError, authorize, html, login_redirect, wants_json
from .public import cached
from .ratelimit import TokenBucket
from .templates.public.errors import error_page
from .templates.public.layout import layout

log = logging.getLogger(__name__)
access_log = logging.getLogger("app.web.access")

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY",
    "Content-Security-Policy": (
        "default-src 'self'; style-src 'self'; script-src 'self'; font-src 'self'; "
        "img-src 'self' data: https://cdn.discordapp.com; connect-src 'self'; "
        "base-uri 'none'; form-action 'self'; frame-ancestors 'none'; object-src 'none'"
    ),
}


def error_response(request: web.Request, exc: WebError) -> web.Response:
    if exc.status == 401:
        return web.json_response({"error": "auth"}, status=401) if wants_json(request) else login_redirect(request)
    if wants_json(request):
        return web.json_response({"error": exc.error, "message": exc.message, **exc.data}, status=exc.status)
    if request.path in ("/login", "/auth/callback"):
        from .auth import login_response
        return login_response(request, exc.message, status=exc.status)
    body = layout(title="Page not found" if exc.status == 404 else "Please try again", description=exc.message, content=error_page(exc.status, exc.message), base_url=request.app["bot"].cfg.public_base_url, path=request.path, private=True)
    return html(body, exc.status)


async def prepare_response(request: web.Request, response: web.StreamResponse) -> None:
    for key, value in SECURITY_HEADERS.items():
        response.headers.setdefault(key, value)
    response.headers.setdefault("Cache-Control", "no-store")
    response.headers["X-Request-ID"] = request.get("request_id", "")


@web.middleware
async def context_middleware(request: web.Request, handler):
    request["request_id"] = secrets.token_hex(8)
    request["activity_service"] = request.app["bot"].activity_service
    started = time.perf_counter()
    response = None
    try:
        response = await handler(request)
        if not response.prepared:
            sessions = request.app["bot"].sessions
            if request.get("clear_session"):
                sessions.clear_cookie(response)
            elif request.get("session"):
                sessions.set_cookie(response, request.cookies[sessions.cookie_name], request["session"])
            if request.get("clear_oauth"):
                sessions.clear_oauth_cookie(response)
        return response
    finally:
        # Paths only: OAuth codes, old query credentials, cookies and headers stay out.
        access_log.info("%s %s %s %dms request=%s", request.method, request.path, getattr(response, "status", 500), int((time.perf_counter() - started) * 1000), request["request_id"])


def accepts_gzip(value: str) -> bool:
    weights = {}
    for part in value.lower().split(","):
        name, *params = part.strip().split(";")
        weight = 1.0
        for param in params:
            if param.strip().startswith("q="):
                try:
                    weight = float(param.strip()[2:])
                except ValueError:
                    weight = 0.0
        weights[name] = weight
    return weights.get("gzip", weights.get("*", 0)) > 0


@web.middleware
async def gzip_middleware(request: web.Request, handler):
    response = await handler(request)
    if not isinstance(response, web.Response) or response.prepared or response.status in (204, 304):
        return response
    textual = response.content_type.startswith("text/") or response.content_type in ("application/json", "application/javascript", "application/xml", "image/svg+xml")
    if textual and isinstance(response.body, bytes) and len(response.body) > 1024:
        response.headers["Vary"] = "Accept-Encoding"
        if accepts_gzip(request.headers.get("Accept-Encoding", "")) and "Content-Encoding" not in response.headers:
            compressor = zlib.compressobj(6, zlib.DEFLATED, 31)
            response.body = compressor.compress(response.body) + compressor.flush()
            response.headers["Content-Encoding"] = "gzip"
            response.headers.pop("Content-Length", None)
    return response


@web.middleware
async def error_middleware(request: web.Request, handler):
    try:
        return await handler(request)
    except WebError as exc:
        return error_response(request, exc)
    except web.HTTPException as exc:
        if exc.status < 400:
            return exc
        response = error_response(request, WebError(exc.status, "http_error", "That page was not found." if exc.status == 404 else exc.reason))
        if "Allow" in exc.headers:
            response.headers["Allow"] = exc.headers["Allow"]
        return response
    except sqlite3.Error:
        log.exception("database HTTP failure request=%s", request["request_id"])
        return error_response(request, WebError(503, "unavailable", "The change was not saved." if request.method == "POST" else "The database is temporarily unavailable. Try again."))
    except Exception:
        log.exception("HTTP failure request=%s path=%s", request["request_id"], request.path)
        return error_response(request, WebError(500, "error", "Something went wrong. The bot log has the rest."))


@web.middleware
async def request_guard_middleware(request: web.Request, handler):
    if request.path == "/dashboard/events":
        return await handler(request)  # its own semaphore; never occupies a general slot
    slots = request.app["connection_slots"]
    if slots.locked():
        response = error_response(request, WebError(503, "busy", "The server is busy. Try again."))
        response.headers["Retry-After"] = "1"
        return response
    await slots.acquire()
    try:
        try:
            return await asyncio.wait_for(handler(request), timeout=request.app["request_timeout"])
        except asyncio.TimeoutError:
            stream = request.get("stream_response")
            if stream is not None and stream.prepared:
                stream.force_close()
                return stream
            raise WebError(504, "timeout", "The request took too long. Reload to check the result.")
    finally:
        slots.release()


@web.middleware
async def rate_limit_middleware(request: web.Request, handler):
    ip = remote_ip(request, request.app["bot"].cfg.trust_proxy) or "unknown"
    allowed, retry = request.app["rate_bucket"].allow(ip)
    if allowed and request.path in ("/login", "/auth/callback"):
        allowed, retry = request.app["auth_bucket"].allow(ip)
    if not allowed:
        response = error_response(request, WebError(429, "rate_limited", "Too many attempts. Wait a minute."))
        response.headers["Retry-After"] = str(max(1, int(retry + .999)))
        return response
    return await handler(request)


@web.middleware
async def authentication_middleware(request: web.Request, handler):
    if request.path in ("/logout", "/change-password") or request.path == "/dashboard" or request.path.startswith("/dashboard/"):
        await authorize(request)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            allowed, retry = request.app["mutation_bucket"].allow(request["session"].id_hash)
            if not allowed:
                response = error_response(request, WebError(429, "rate_limited", "Too many changes. Wait a minute."))
                response.headers["Retry-After"] = str(max(1, int(retry + .999)))
                return response
    return await handler(request)


async def health(request: web.Request) -> web.Response:
    return web.json_response(await request.app["bot"].status_service.health())


async def status(request: web.Request) -> web.Response:
    payload = await request.app["bot"].status_service.public_snapshot()
    return cached(request, json.dumps(payload, separators=(",", ":")), version=payload["checked_at"], content_type="application/json", cache="public, max-age=15, stale-while-revalidate=15", status=503 if payload["status"] == "offline" else 200)


async def asset(request: web.Request) -> web.Response:
    name = request.match_info["name"]
    if name not in ASSETS:
        raise WebError(404, "not_found", "That asset was not found.")
    body, content_type = ASSETS[name]
    return cached(request, body, version=name, content_type=content_type, cache="public, max-age=31536000, immutable")


async def favicon(request: web.Request) -> web.Response:
    return cached(request, FAVICON_BYTES, version=FAVICON_NAME, content_type="image/svg+xml")


async def not_found(request: web.Request) -> web.Response:
    raise WebError(404, "not_found", "That page was not found.")


async def http_client(app: web.Application):
    async with ClientSession(timeout=ClientTimeout(total=8)) as client:
        app["oauth_client"] = client
        yield


async def shutdown_streams(app: web.Application) -> None:
    tasks = list(app["sse_tasks"])
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def create_app(bot: Any) -> web.Application:
    from . import accounts, activity, auth, dashboard, public
    app = web.Application(client_max_size=32768, middlewares=[context_middleware, gzip_middleware, error_middleware, request_guard_middleware, rate_limit_middleware, authentication_middleware])
    app["bot"] = bot
    app["rate_bucket"] = TokenBucket(bot.cfg.rate_limit_per_min, bot.cfg.rate_limit_burst)
    app["auth_bucket"] = TokenBucket(20, 5)
    app["mutation_bucket"] = TokenBucket(30, 30)
    app["lookup_bucket"] = TokenBucket(10, 10)
    app["connection_slots"] = asyncio.Semaphore(bot.cfg.max_connections)
    app["sse_slots"] = asyncio.Semaphore(8)
    app["sse_tasks"] = set()
    app["request_timeout"] = float(bot.cfg.request_timeout_s)
    app.cleanup_ctx.append(http_client)
    app.on_shutdown.append(shutdown_streams)
    app.on_response_prepare.append(prepare_response)
    app.router.add_get("/health", health)
    app.router.add_get("/status", status)
    app.router.add_get("/assets/{name}", asset)
    app.router.add_get("/favicon.svg", favicon)
    for module in (public, auth, dashboard, accounts, activity):
        module.setup_routes(app)
    app.router.add_route("*", "/{tail:.*}", not_found)
    return app


async def start_server(bot: Any) -> tuple[web.AppRunner | None, str | None]:
    runner = web.AppRunner(create_app(bot), access_log=None)
    try:
        await runner.setup()
        await web.TCPSite(runner, bot.cfg.http_host, bot.cfg.http_port, backlog=max(32, bot.cfg.max_connections)).start()
    except OSError as exc:
        log.error("HTTP bind failed on %s:%s (%s); check HTTP_HOST and the port", bot.cfg.http_host, bot.cfg.http_port, exc)
        await runner.cleanup()
        return None, str(exc)
    log.info("HTTP listening on %s:%s; public site and dashboard mounted", bot.cfg.http_host, bot.cfg.http_port)
    return runner, None
