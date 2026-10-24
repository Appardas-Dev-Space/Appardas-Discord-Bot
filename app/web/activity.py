"""Owner-only activity screen and the exchange-safe CSV export."""

from __future__ import annotations

import csv
import io
import logging
from typing import Any

from aiohttp import web

from .common import WebError, accessible_guilds, html
from .templates.app.admin_activity import activity_page
from .templates.base import layout

log = logging.getLogger(__name__)

CSV_COLUMNS = (
    "id", "created_at", "request_id", "account_id", "discord_user_id", "account_label",
    "role", "action", "outcome", "guild_id", "target", "before_json", "after_json",
    "ip", "user_agent", "detail",
)


def filters(request: web.Request) -> dict[str, str]:
    try:
        return request.app["bot"].activity_service.filters(request.query)
    except (ValueError, TypeError) as exc:
        raise WebError(400, "bad_request", str(exc)) from exc


async def list_activity(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    query = filters(request)
    rows = await bot.activity_service.query(query)
    accounts = await bot.accounts.list()
    guilds = await bot.db.fetchall("SELECT id, name, left_at FROM guilds ORDER BY lower(name), id")
    content = activity_page(rows=rows, accounts=accounts, guilds=guilds, query=query, total=await bot.activity_service.count(query))
    body = layout(
        title="Activity",
        active="activity",
        content=content,
        account=request["account"],
        csrf=request["session"].csrf,
        guilds=await accessible_guilds(request),
    )
    return html(body)


async def export_csv(request: web.Request) -> web.StreamResponse:
    bot, query = request.app["bot"], filters(request)
    response = web.StreamResponse(headers={
        "Content-Type": "text/csv; charset=utf-8",
        "Content-Disposition": 'attachment; filename="appardas-activity.csv"',
        "Cache-Control": "no-store",
    })
    await response.prepare(request)
    await response.write(("\ufeff" + ",".join(CSV_COLUMNS) + "\r\n").encode("utf-8"))
    buffer, exported, cursor = io.StringIO(), 0, query.get("before")
    while exported < 50_000:
        batch_query = dict(query, limit="200", **({"before": cursor} if cursor else {}))
        rows = await bot.activity_service.query(batch_query, limit=min(500, 50_000 - exported))
        if not rows:
            break
        writer = csv.writer(buffer)
        for row in rows:
            writer.writerow(["" if row.get(column) is None else row[column] for column in CSV_COLUMNS])
        exported += len(rows)
        cursor = str(rows[-1]["id"])
        if len(rows) < 200:
            break
        if len(buffer.getvalue()) > 32768:
            await response.write(buffer.getvalue().encode("utf-8"))
            buffer.seek(0)
            buffer.truncate(0)
    if buffer.getvalue():
        await response.write(buffer.getvalue().encode("utf-8"))
    await response.write_eof()
    return response


def setup_routes(app: web.Application) -> None:
    app.router.add_get("/dashboard/admin/activity", list_activity)
    app.router.add_get("/dashboard/admin/activity.csv", export_csv)
