"""Guild-granted console, shared settings writes and committed live events."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import sqlite3
from typing import Any
from urllib.parse import urlencode

from aiohttp import web

from ..cogs.registry import COGS
from ..services.activity import record_activity
from ..services.settings import CHANNEL_KEYS, CONFIG_KEYS, LockedCogError
from ..util import utcnow
from .common import WebError, accessible_guilds, authorize, form_data, html, log_denial, mutation, positive_id, saved, wants_json
from .templates.app.announcements_page import announcements_page
from .templates.app.commands_page import commands_page
from .templates.base import layout
from .templates.pages import SETTING_LABELS, audit_page, cogs_page, notice, overview_page, settings_page, title_row, watches_page

log = logging.getLogger(__name__)


async def render(request: web.Request, title: str, content: str, *, active: str, snapshot: dict | None = None, status: int = 200, live: bool = False) -> web.Response:
    return html(layout(title=title, active=active, content=content, account=request["account"], csrf=request["session"].csrf, guilds=await accessible_guilds(request), guild=request.get("guild"), verdict=(snapshot or {}).get("status", ""), live=live), status)


def actor_name(bot: Any, user_id: int | None) -> str:
    if user_id is None:
        return "system"
    user = bot.get_user(user_id)
    return "@" + user.name if user else f"user {user_id}"


def audit_actors(bot: Any, rows: list[dict]) -> list[dict]:
    return [dict(row, actor=actor_name(bot, row["actor_id"])) for row in rows]


async def guild_context(request: web.Request) -> dict[str, Any]:
    bot, guild = request.app["bot"], request["guild"]
    states = await bot.settings_service.cog_states(guild.id)
    return {"guild": guild, "states": states, "csrf": request["session"].csrf, "writable": request["account"].can_write, "actors": {state.updated_by: actor_name(bot, state.updated_by) for state in states.values()}, "held": await bot.announcements.pending_count(guild.id)}


async def dashboard_root(request: web.Request) -> web.Response:
    guilds = await accessible_guilds(request)
    if guilds:
        last = request["session"].last_guild_id
        selected = last if any(guild["id"] == last for guild in guilds) else guilds[0]["id"]
        return web.HTTPFound(f"/dashboard/g/{selected}")
    return await render(request, "Servers", title_row("No servers assigned yet.", "Ask the owner to grant you a server."), active="overview")


async def dashboard_overview(request: web.Request) -> web.Response:
    bot, guild = request.app["bot"], request["guild"]
    snapshot = await bot.status_service.snapshot()
    content = overview_page(**await guild_context(request), snapshot=snapshot, last_renewed=await bot.db.get_meta("last_renewed_at"), heartbeat=await bot.status_service.heartbeat_series(), audit_rows=audit_actors(bot, await bot.audit.tail(guild.id, 6)))
    return await render(request, "Overview", content, active="overview", snapshot=snapshot, live=True)


async def dashboard_cogs(request: web.Request, *, message: str = "", status: int = 200) -> web.Response:
    if not message and request.query.get("saved") in COGS:
        message = f'{request.query["saved"]} saved.'
    content = cogs_page(**await guild_context(request), message=message, error=status >= 400)
    return await render(request, "Cogs", content, active="cogs", status=status, live=True)


async def dashboard_settings(request: web.Request, *, message: str = "", errors: dict | None = None, status: int = 200, values: dict | None = None) -> web.Response:
    bot, guild = request.app["bot"], request["guild"]
    if not message and request.query.get("saved") in CONFIG_KEYS:
        message = f'{SETTING_LABELS[request.query["saved"]]} saved.'
    current = dict(await bot.settings_service.settings_row(guild.id, use_cache=False))
    current.update(values or {})
    content = settings_page(guild=guild, values=current, csrf=request["session"].csrf, writable=request["account"].can_write, channels=sorted(guild.text_channels, key=lambda channel: channel.name.casefold()), connected=bot.is_ready(), message=message, errors=errors)
    return await render(request, "Settings", content, active="settings", status=status, live=True)


async def dashboard_watches(request: web.Request) -> web.Response:
    bot, guild = request.app["bot"], request["guild"]
    watches = await bot.github.list_watches(guild.id)
    content = watches_page(guild=guild, watches=watches, health={row["id"]: bot.github.display_state(row) for row in watches}, channels={channel.id: "#" + channel.name for channel in guild.text_channels})
    return await render(request, "Watches", content, active="watches", live=True)


async def dashboard_audit(request: web.Request) -> web.Response:
    bot, guild = request.app["bot"], request["guild"]
    action = request.query.get("action", "")[:200]
    limit = int(request.query.get("limit", "50")) if request.query.get("limit", "50") in ("50", "100", "200") else 50
    before = positive_id(request.query["before"]) if request.query.get("before") else None
    rows = audit_actors(bot, await bot.audit.tail(guild.id, limit, action=action, before=before))
    actions = [row["action"] for row in await bot.db.fetchall("SELECT DISTINCT action FROM audit_log WHERE guild_id = ? ORDER BY action", (guild.id,))]
    content = audit_page(guild=guild, rows=rows, actions=actions, action=action, limit=limit, owner=request["account"].role == "owner")
    return await render(request, "Audit", content, active="audit")


async def dashboard_announcements(
    request: web.Request, *, message: str = "", status: int = 200
) -> web.Response:
    bot, guild = request.app["bot"], request["guild"]
    if not message and request.query.get("saved"):
        message = f"Announcement {request.query['saved']}."
    if not message and request.query.get("message"):
        message = request.query["message"]
    pending = await bot.announcements.list_pending(guild.id)
    recent = await bot.announcements.list_recent(guild.id, 25)
    channels = sorted(guild.text_channels, key=lambda c: c.name.casefold())
    roles = sorted(guild.roles, key=lambda r: r.position, reverse=True)
    content = announcements_page(
        guild=guild,
        pending=pending,
        recent=recent,
        channels=channels,
        roles=roles,
        csrf=request["session"].csrf,
        writable=request["account"].can_write,
        connected=bot.is_ready(),
        message=message,
        error=status >= 400 or request.query.get("error") == "1",
    )
    return await render(request, "Announcements", content, active="announcements", status=status, live=True)


async def post_announcement(request: web.Request) -> web.Response:
    bot, guild = request.app["bot"], request["guild"]
    tz_str = await bot.settings_service.get_setting(guild.id, "timezone") or "UTC"
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    try:
        tz = ZoneInfo(tz_str)
    except (ZoneInfoNotFoundError, ValueError):
        tz = ZoneInfo("UTC")

    created_id: int | None = None
    should_dispatch_now: bool = False

    async def operation(conn):
        nonlocal created_id, should_dispatch_now
        form = await form_data(request)
        channel_id_raw = form.get("channel_id")
        if not channel_id_raw:
            raise ValueError("Select a destination channel.")
        channel_id = positive_id(channel_id_raw)
        title = form.get("title", "").strip()
        if not title:
            raise ValueError("Title is required.")
        body = form.get("body", "").strip()
        if not body:
            raise ValueError("Message body is required.")

        when_raw = form.get("when", "now").strip()
        action_type = form.get("action_type", "schedule")

        if action_type == "send_now" or when_raw.lower() in ("now", "immediately", "0", ""):
            send_at = utcnow()
            should_dispatch_now = True
        else:
            from ..services.announcements import AnnouncementError, parse_when
            try:
                send_at = parse_when(when_raw, tz)
            except AnnouncementError as err:
                raise ValueError(str(err)) from err

        ping_role_id = form.get("ping_role_id", "").strip() or None
        image_url = form.get("image_url", "").strip() or None
        footer_text = form.get("footer_text", "").strip() or None
        color = form.get("color", "").strip() or None

        fields = []
        fields_text = form.get("fields_text", "").strip()
        if fields_text:
            for line in fields_text.splitlines():
                line = line.strip()
                if not line:
                    continue
                if "|" in line:
                    fname, _, fval = line.partition("|")
                    fields.append({"name": fname.strip(), "value": fval.strip(), "inline": False})
                elif line.startswith("- "):
                    fields.append({"name": "•", "value": line[2:].strip(), "inline": False})
                else:
                    fields.append({"name": "Notice", "value": line, "inline": False})

        author_id = request["account"].discord_user_id or 0
        created_id = await bot.announcements.create(
            guild_id=guild.id,
            channel_id=channel_id,
            author_id=author_id,
            title=title,
            body=body,
            send_at=send_at,
            ping_role_id=ping_role_id,
            image_url=image_url,
            footer_text=footer_text,
            fields=fields if fields else None,
            color=color,
            source="web",
            conn=conn,
        )
        activity_id = await record_activity(
            request=request,
            action="announcement.create",
            guild_id=guild.id,
            target=str(created_id),
            detail={
                "title": title,
                "channel_id": channel_id,
                "send_at": send_at.isoformat(),
                "immediate": should_dispatch_now,
            },
            conn=conn,
        )
        return {"id": created_id, "activity_id": activity_id, "message": "Announcement scheduled."}

    try:
        result = await mutation(request, operation)
    except Exception as exc:
        return await _mutation_error(request, exc, action="announcement.create", target=str(guild.id))

    if should_dispatch_now and created_id is not None:
        try:
            ok = await bot.announcements.dispatch_one(bot, created_id)
            msg = "Announcement sent immediately." if ok else "Announcement created, but dispatch failed."
        except Exception as exc:
            log.warning("immediate dispatch error: %s", exc)
            msg = f"Announcement created, but send error: {exc}"
        return saved(request, f"/dashboard/g/{guild.id}/announcements?" + urlencode({"message": msg}), result)

    return saved(request, f"/dashboard/g/{guild.id}/announcements?" + urlencode({"saved": "scheduled"}), result)


async def send_announcement_now(request: web.Request) -> web.Response:
    bot, guild = request.app["bot"], request["guild"]
    announcement_id = positive_id(request.match_info["id"])

    async def operation(conn):
        row = await bot.announcements.get(guild.id, announcement_id, conn=conn)
        if not row:
            raise WebError(404, "not_found", "Announcement not found.")
        if row["status"] != "pending":
            raise ValueError(f"Cannot dispatch announcement with status '{row['status']}'.")
        activity_id = await record_activity(
            request=request,
            action="announcement.send_now",
            guild_id=guild.id,
            target=str(announcement_id),
            conn=conn,
        )
        return {"id": announcement_id, "activity_id": activity_id}

    try:
        result = await mutation(request, operation)
    except Exception as exc:
        return await _mutation_error(request, exc, action="announcement.send_now", target=str(announcement_id))

    ok = await bot.announcements.dispatch_one(bot, announcement_id)
    msg = "Announcement dispatched." if ok else "Announcement dispatch failed. Check channel permissions."
    return saved(request, f"/dashboard/g/{guild.id}/announcements?" + urlencode({"message": msg}), result)


async def cancel_announcement(request: web.Request) -> web.Response:
    bot, guild = request.app["bot"], request["guild"]
    announcement_id = positive_id(request.match_info["id"])

    async def operation(conn):
        author_id = request["account"].discord_user_id or 0
        cancelled = await bot.announcements.cancel(guild.id, announcement_id, author_id, conn=conn)
        if not cancelled:
            raise WebError(404, "not_found", "Announcement not found or not in pending state.")
        activity_id = await record_activity(
            request=request,
            action="announcement.cancel",
            guild_id=guild.id,
            target=str(announcement_id),
            conn=conn,
        )
        return {"id": announcement_id, "activity_id": activity_id, "message": "Announcement cancelled."}

    try:
        result = await mutation(request, operation)
    except Exception as exc:
        return await _mutation_error(request, exc, action="announcement.cancel", target=str(announcement_id))

    return saved(request, f"/dashboard/g/{guild.id}/announcements?" + urlencode({"saved": "cancelled"}), result)


async def edit_announcement(request: web.Request) -> web.Response:
    bot, guild = request.app["bot"], request["guild"]
    announcement_id = positive_id(request.match_info["id"])
    tz_str = await bot.settings_service.get_setting(guild.id, "timezone") or "UTC"
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    try:
        tz = ZoneInfo(tz_str)
    except (ZoneInfoNotFoundError, ValueError):
        tz = ZoneInfo("UTC")

    async def operation(conn):
        row = await bot.announcements.get(guild.id, announcement_id, conn=conn)
        if not row:
            raise WebError(404, "not_found", "Announcement not found.")
        if row["status"] != "pending":
            raise ValueError(f"Cannot edit announcement with status '{row['status']}'.")

        form = await form_data(request)
        channel_id_raw = form.get("channel_id")
        channel_id = positive_id(channel_id_raw) if channel_id_raw else None
        title = form.get("title", "").strip() or None
        body = form.get("body", "").strip() or None

        when_raw = form.get("when", "").strip()
        send_at = None
        if when_raw:
            from ..services.announcements import AnnouncementError, parse_when
            try:
                send_at = parse_when(when_raw, tz)
            except AnnouncementError as err:
                raise ValueError(str(err)) from err

        ping_role_id = form.get("ping_role_id", "").strip() or None
        image_url = form.get("image_url", "").strip() or None
        footer_text = form.get("footer_text", "").strip() or None
        color = form.get("color", "").strip() or None

        fields = None
        fields_text = form.get("fields_text", "").strip()
        if fields_text:
            fields = []
            for line in fields_text.splitlines():
                line = line.strip()
                if not line:
                    continue
                if "|" in line:
                    fname, _, fval = line.partition("|")
                    fields.append({"name": fname.strip(), "value": fval.strip(), "inline": False})
                elif line.startswith("- "):
                    fields.append({"name": "•", "value": line[2:].strip(), "inline": False})
                else:
                    fields.append({"name": "Notice", "value": line, "inline": False})

        author_id = request["account"].discord_user_id or 0
        updated = await bot.announcements.update(
            guild_id=guild.id,
            announcement_id=announcement_id,
            actor_id=author_id,
            title=title,
            body=body,
            channel_id=channel_id,
            send_at=send_at,
            ping_role_id=ping_role_id,
            image_url=image_url,
            footer_text=footer_text,
            fields=fields,
            color=color,
            source="web",
            conn=conn,
        )
        if not updated:
            raise WebError(400, "cannot_update", "Announcement update failed.")

        activity_id = await record_activity(
            request=request,
            action="announcement.update",
            guild_id=guild.id,
            target=str(announcement_id),
            detail={"title": title, "channel_id": channel_id},
            conn=conn,
        )
        return {"id": announcement_id, "activity_id": activity_id, "message": "Announcement updated."}

    try:
        result = await mutation(request, operation)
    except Exception as exc:
        return await _mutation_error(request, exc, action="announcement.update", target=str(announcement_id))

    return saved(request, f"/dashboard/g/{guild.id}/announcements?" + urlencode({"saved": "updated"}), result)


async def dashboard_commands(request: web.Request) -> web.Response:
    bot, guild = request.app["bot"], request["guild"]
    cmd_filter = request.query.get("command", "")[:100]
    user_filter_raw = request.query.get("user_id", "").strip()
    user_id = positive_id(user_filter_raw) if user_filter_raw.isdigit() else None
    status_filter = request.query.get("status", "")[:20]
    limit = (
        int(request.query.get("limit", "50"))
        if request.query.get("limit", "50") in ("50", "100", "200")
        else 50
    )
    before = (
        positive_id(request.query["before"])
        if request.query.get("before") and request.query["before"].isdigit()
        else None
    )

    rows = await bot.command_log.tail(
        guild.id,
        limit=limit,
        command=cmd_filter,
        user_id=user_id,
        status=status_filter,
        before=before,
    )
    distinct_cmds = await bot.command_log.distinct_commands(guild.id)
    channels = {channel.id: "#" + channel.name for channel in guild.text_channels}

    content = commands_page(
        guild=guild,
        rows=rows,
        distinct_commands=distinct_cmds,
        command=cmd_filter,
        user_id=user_id,
        status=status_filter,
        limit=limit,
        channels=channels,
    )
    return await render(request, "Command Log", content, active="commands")


async def _mutation_error(request: web.Request, exc: Exception, *, action: str, target: str) -> web.Response:
    from .status import error_response
    if isinstance(exc, WebError):
        if exc.status in (401, 404):
            raise exc
        failure = exc
    else:
        status = 409 if isinstance(exc, LockedCogError) else 400 if isinstance(exc, (ValueError, KeyError)) else 503
        message = "This cog is locked. It keeps the bot recoverable." if isinstance(exc, LockedCogError) else str(exc) if status == 400 else "The change was not saved."
        failure = WebError(status, "locked_cog" if status == 409 else "invalid" if status == 400 else "unavailable", message)
        await log_denial(request, action=action, outcome="error", guild_id=request["guild"].id, target=target, detail={"reason": "validation" if status < 500 else "write_failed"})
        if status >= 500:
            log.exception("guild mutation failed request=%s", request["request_id"])
    if wants_json(request):
        return error_response(request, failure)
    if action == "cog.toggle":
        return await dashboard_cogs(request, message=failure.message, status=failure.status)
    if action.startswith("announcement."):
        return await dashboard_announcements(request, message=failure.message, status=failure.status)
    form = request.get("form", {})
    column = "mod_log_channel" if target == "mod_log" else target
    return await dashboard_settings(request, errors={target: failure.message}, status=failure.status, values={column: form.get("value", "")})


async def toggle_cog(request: web.Request) -> web.Response:
    bot, guild = request.app["bot"], request["guild"]
    cog = request.match_info["cog"]
    async def operation(conn):
        if cog not in COGS:
            raise WebError(404, "not_found", "That cog was not found.")
        if COGS[cog].locked:
            raise LockedCogError(cog)
        form = await form_data(request)
        if form.get("enabled") not in ("0", "1"):
            raise ValueError("Choose on or off.")
        enabled = form["enabled"] == "1"
        before = await bot.db.fetchone_on(conn, "SELECT enabled FROM cog_state WHERE guild_id = ? AND cog = ?", (guild.id, cog))
        state = await bot.settings_service.set_cog_state(guild.id, cog, enabled, request["account"].discord_user_id, reason=form.get("reason", ""), source="web", conn=conn, request_id=request["request_id"])
        activity_id = await record_activity(request=request, action="cog.toggle", guild_id=guild.id, target=cog, before={"enabled": bool(before["enabled"]) if before else True}, after={"enabled": enabled}, detail={"reason": form.get("reason", "")}, conn=conn)
        return {"cog": cog, "enabled": state.enabled, "state": state.state, "locked": state.locked, "audit_id": state.audit_id, "activity_id": activity_id, "held_announcements": state.held_announcements, "message": f"{cog} saved."}
    try:
        result = await mutation(request, operation)
    except Exception as exc:
        return await _mutation_error(request, exc, action="cog.toggle", target=cog)
    return saved(request, f"/dashboard/g/{guild.id}/cogs?" + urlencode({"saved": cog}), result)


async def set_setting(request: web.Request) -> web.Response:
    bot, guild = request.app["bot"], request["guild"]
    key = request.match_info["key"]
    async def operation(conn):
        if key not in CONFIG_KEYS:
            raise WebError(404, "not_found", "That setting was not found.")
        form = await form_data(request)
        value = None if form.get("clear") == "1" else form.get("value", "")
        if key == "prefix" and isinstance(value, str):
            trimmed = value.strip()
            value = None if not trimmed or trimmed.lower() in ("reset", "none", "clear") else trimmed
        if key in CHANNEL_KEYS:
            if value:
                try:
                    value = positive_id(value)
                except WebError as exc:
                    raise ValueError("Choose a text channel in this server.") from exc
                current_guild = bot.get_guild(guild.id)
                if current_guild is None or not bot.is_ready() or value not in {channel.id for channel in current_guild.text_channels}:
                    raise ValueError("Choose a text channel in this server.")
            else:
                value = None
        before = await bot.settings_service.get_setting(guild.id, key, conn=conn)
        value = await bot.settings_service.set_setting(guild.id, key, value, request["account"].discord_user_id, source="web", conn=conn, request_id=request["request_id"])
        row = await bot.db.fetchone_on(conn, "SELECT id FROM audit_log WHERE guild_id = ? AND action = 'config.set' AND json_extract(detail, '$.request_id') = ? ORDER BY id DESC LIMIT 1", (guild.id, request["request_id"]))
        activity_id = await record_activity(request=request, action="config.set", guild_id=guild.id, target=key, before={key: before}, after={key: value}, conn=conn)
        display = "mention-only" if key == "prefix" and value is None else "not set" if value is None else str(value)
        message = f"{SETTING_LABELS[key]} saved. New prefix: {display}." if key == "prefix" else f"{SETTING_LABELS[key]} saved."
        return {"key": key, "value": str(value) if key in CHANNEL_KEYS and value else value, "display": display, "audit_id": row["id"], "activity_id": activity_id, "message": message}
    try:
        result = await mutation(request, operation)
    except Exception as exc:
        return await _mutation_error(request, exc, action="config.set", target=key)
    return saved(request, f"/dashboard/g/{guild.id}/settings?" + urlencode({"saved": key}), result)


async def private_status(request: web.Request) -> web.Response:
    return web.json_response(await request.app["bot"].status_service.snapshot())


async def events(request: web.Request) -> web.StreamResponse:
    bot, slots = request.app["bot"], request.app["sse_slots"]
    if slots.locked():
        return web.json_response({"error": "busy"}, status=503, headers={"Retry-After": "20"})
    await slots.acquire()
    task = asyncio.current_task()
    request.app["sse_tasks"].add(task)
    queue = bot.event_bus.subscribe()
    response = web.StreamResponse(headers={"Content-Type": "text/event-stream", "Cache-Control": "no-store", "X-Accel-Buffering": "no"})
    async def send(name: str, data: dict):
        await response.write(f"event: {name}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n".encode())
    try:
        await response.prepare(request)
        await send("hello", {"status": "connected"})
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=20)
            except asyncio.TimeoutError:
                event = None
            try:
                await authorize(request)
            except WebError:
                await send("auth", {"error": "auth"})
                break
            if event is None:
                await response.write(b": ping\n\n")
                continue
            name = event.get("type")
            if name not in ("cog.toggle", "config.set", "heartbeat"):
                continue
            guild_id = event.get("guild_id")
            if guild_id is not None:
                if bot.get_guild(guild_id) is None or not await bot.accounts.has_grant(request["account"], guild_id):
                    continue
            elif name != "heartbeat":
                continue
            payload = {key: value for key, value in event.items() if key in ("guild_id", "cog", "enabled", "state", "key", "value", "source", "request_id")}
            # Snowflakes exceed JavaScript's exact integer range.
            if guild_id is not None:
                payload["guild_id"] = str(guild_id)
            await send(name, payload)
    except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
        pass
    finally:
        bot.event_bus.unsubscribe(queue)
        request.app["sse_tasks"].discard(task)
        slots.release()
        with contextlib.suppress(ConnectionError, RuntimeError):
            await response.write_eof()
    return response


def setup_routes(app: web.Application) -> None:
    app.router.add_get("/dashboard", dashboard_root)
    app.router.add_get("/dashboard/status.json", private_status)
    app.router.add_get("/dashboard/events", events, allow_head=False)
    app.router.add_get("/dashboard/g/{guild_id}", dashboard_overview)
    for path, handler in (
        ("announcements", dashboard_announcements),
        ("cogs", dashboard_cogs),
        ("settings", dashboard_settings),
        ("watches", dashboard_watches),
        ("commands", dashboard_commands),
        ("audit", dashboard_audit),
    ):
        app.router.add_get("/dashboard/g/{guild_id}/" + path, handler)
    app.router.add_post("/dashboard/g/{guild_id}/announcements", post_announcement)
    app.router.add_post("/dashboard/g/{guild_id}/announcements/{id}/send", send_announcement_now)
    app.router.add_post("/dashboard/g/{guild_id}/announcements/{id}/cancel", cancel_announcement)
    app.router.add_post("/dashboard/g/{guild_id}/announcements/{id}/edit", edit_announcement)
    app.router.add_post("/dashboard/g/{guild_id}/cogs/{cog}", toggle_cog)
    app.router.add_post("/dashboard/g/{guild_id}/settings/{key}", set_setting)
