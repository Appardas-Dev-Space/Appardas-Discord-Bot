"""Owner-only presence management in the web dashboard."""

from __future__ import annotations

import contextlib
import logging
from typing import Any

from aiohttp import web

from ..services.activity import record_activity
from .common import WebError, accessible_guilds, form_data, html, mutation, wants_json
from .templates.base import csrf_field, e, layout
from .templates.pages import notice, select_options, title_row

log = logging.getLogger(__name__)

TYPE_OPTIONS = [
    ("custom", "Custom (Bio text only, no prefix)"),
    ("watching", "Watching"),
    ("playing", "Playing"),
    ("listening", "Listening to"),
    ("competing", "Competing in"),
]

STATUS_OPTIONS = [
    ("online", "Online (Green)"),
    ("idle", "Idle (Orange moon)"),
    ("dnd", "Do Not Disturb (Red)"),
    ("invisible", "Invisible (Appear offline)"),
]


def presence_page(*, info: dict[str, Any], csrf: str, message: str = "", error: bool = False) -> str:
    current_source = "Custom override (saved in database)" if info.get("is_custom") else "Default (environment configuration)"

    preview_box = f'''
    <section class="panel" style="margin-bottom: 24px;">
      <h2>Current live presence</h2>
      <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-top: 12px;">
        <div>
          <span class="muted" style="font-size: 12px; text-transform: uppercase;">Activity text</span>
          <p style="font-size: 18px; font-weight: 600; margin: 4px 0;">{e(info.get("text", "—"))}</p>
        </div>
        <div>
          <span class="muted" style="font-size: 12px; text-transform: uppercase;">Type</span>
          <p style="font-size: 18px; font-weight: 600; margin: 4px 0;">{e(info.get("type", "—"))}</p>
        </div>
        <div>
          <span class="muted" style="font-size: 12px; text-transform: uppercase;">Status</span>
          <p style="font-size: 18px; font-weight: 600; margin: 4px 0;">{e(info.get("status", "—"))}</p>
        </div>
        <div>
          <span class="muted" style="font-size: 12px; text-transform: uppercase;">Configuration source</span>
          <p style="font-size: 14px; margin: 4px 0; color: var(--muted);">{e(current_source)}</p>
        </div>
      </div>
    </section>
    '''

    form_box = f'''
    <section class="panel">
      <h2>Change presence</h2>
      <p class="muted">Updates the bot’s status bio and activity across Discord immediately and persists across restarts.</p>
      <form method="post" action="/dashboard/admin/presence" class="setting-form" style="max-width: 600px; margin-top: 16px;">
        {csrf_field(csrf)}
        <div class="field" style="margin-bottom: 16px;">
          <label for="presence-text">Status bio / Activity text</label>
          <input id="presence-text" name="text" value="{e(info.get("text", ""))}" maxlength="128" placeholder="e.g. ⚠️ Production Degraded - Investigating" required>
          <p class="muted" style="font-size: 12px; margin-top: 4px;">For custom bio text or service status alerts. Max 128 characters.</p>
        </div>
        <div class="field" style="margin-bottom: 16px;">
          <label for="presence-type">Activity type</label>
          <select id="presence-type" name="type">
            {select_options(TYPE_OPTIONS, info.get("type", "custom"))}
          </select>
        </div>
        <div class="field" style="margin-bottom: 20px;">
          <label for="presence-status">Online status</label>
          <select id="presence-status" name="status">
            {select_options(STATUS_OPTIONS, info.get("status", "online"))}
          </select>
        </div>
        <div class="setting-actions" style="display: flex; gap: 12px;">
          <button class="plain-button primary" type="submit">Save presence</button>
          <button class="plain-button" name="action" value="reset" type="submit" formnovalidate>Reset to default</button>
        </div>
      </form>
    </section>
    '''

    return (
        title_row("Bot Presence", "Dynamically update status bio, activity and online state across Discord.")
        + notice(message, error=error)
        + preview_box
        + form_box
    )


async def shell(request: web.Request, title: str, content: str, *, active: str, status: int = 200) -> web.Response:
    body = layout(
        title=title,
        active=active,
        content=content,
        account=request["account"],
        csrf=request["session"].csrf,
        guilds=await accessible_guilds(request),
    )
    return html(body, status)


async def get_presence(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    info = await bot.status_service.get_presence()
    message = "Presence saved and live on Discord." if request.query.get("saved") == "1" else ""
    content = presence_page(info=info, csrf=request["session"].csrf, message=message)
    return await shell(request, "Presence", content, active="presence")


async def post_presence(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    form = await form_data(request)
    action = form.get("action")
    if action == "reset":
        text = "reset"
        typ = None
        status = None
    else:
        text = form.get("text", "").strip()
        typ = form.get("type", "custom").strip()
        status = form.get("status", "online").strip()
        if not text:
            raise WebError(400, "invalid", "Status text cannot be empty.")

    async def operation(conn):
        before = await bot.status_service.get_presence(conn=conn)
        info = await bot.status_service.set_presence(text=text, type=typ, status=status, conn=conn)
        await record_activity(
            request=request,
            action="presence.update",
            guild_id=None,
            target=info["text"],
            before=before,
            after=info,
            conn=conn,
        )
        return info

    result = await mutation(request, operation)
    if getattr(bot, "ws", None) is not None:
        activity = bot.status_service.build_activity(result["text"], result["type"])
        discord_status = bot.status_service.resolve_status(result["status"])
        with contextlib.suppress(Exception):
            await bot.change_presence(activity=activity, status=discord_status)
    if wants_json(request):
        return web.json_response(result)
    return web.HTTPSeeOther("/dashboard/admin/presence?saved=1")


def setup_routes(app: web.Application) -> None:
    app.router.add_get("/dashboard/admin/presence", get_presence)
    app.router.add_post("/dashboard/admin/presence", post_presence)
