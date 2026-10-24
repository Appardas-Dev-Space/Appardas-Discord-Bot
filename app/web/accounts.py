"""Owner-only account lifecycle, grants and the identity lookup convenience."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from aiohttp import web

from ..services.accounts import AccountError, validate_discord_id
from ..services.activity import record_activity
from .common import WebError, accessible_guilds, form_data, html, mutation, positive_id, wants_json
from .templates.app.admin_accounts import accounts_page, detail_page, new_account_page
from .templates.base import layout

log = logging.getLogger(__name__)


async def guild_rows(request: web.Request) -> list[dict[str, Any]]:
    return await request.app["bot"].db.fetchall("SELECT id, name, left_at FROM guilds ORDER BY lower(name), id")


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


async def account_rows(request: web.Request) -> tuple[dict[str, Any], int]:
    account_id = positive_id(request.match_info["account_id"])
    if await request.app["bot"].accounts.get(account_id) is None:
        raise WebError(404, "not_found", "That account was not found.")
    row = await request.app["bot"].db.fetchone(
        "SELECT a.*, (SELECT COUNT(*) FROM sessions s WHERE s.account_id = a.id) AS session_count "
        "FROM accounts a WHERE a.id = ?",
        (account_id,),
    )
    if row is None:
        raise WebError(404, "not_found", "That account was not found.")
    return row, int(row["session_count"])


async def list_accounts(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    query = {key: request.query.get(key, "")[:200] for key in ("q", "role", "status")}
    rows = await bot.accounts.list(**query)
    content = accounts_page(rows=rows, bot=bot, query=query, total=len(rows))
    return await shell(request, "Accounts", content, active="accounts")


async def new_account(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    values = {key: request.query.get(key, "")[:200] for key in ("discord_user_id", "name", "role", "note")}
    lookup, lookup_error = None, ""
    if values["discord_user_id"]:
        try:
            discord_id = validate_discord_id(values["discord_user_id"])
        except AccountError as exc:
            lookup_error = str(exc)
        else:
            allowed, _ = request.app["lookup_bucket"].allow(request["session"].id_hash)
            if not allowed:
                lookup_error = "Too many lookups. Wait a minute, or save without verification."
            else:
                try:
                    user = bot.get_user(discord_id)
                    if user is None and bot.is_ready():
                        user = await asyncio.wait_for(bot.fetch_user(discord_id), timeout=8)
                    if user is None:
                        lookup_error = "Discord does not know this ID yet. You can still save without verification."
                    else:
                        lookup = {"username": user.name, "created_at": getattr(user, "created_at", None)}
                        values["name"] = values["name"] or getattr(user, "display_name", user.name)
                except Exception:
                    lookup_error = "Discord could not verify this ID. You can still save without verification."
    content = new_account_page(
        guilds=await guild_rows(request),
        csrf=request["session"].csrf,
        values=values,
        lookup=lookup,
        lookup_error=lookup_error,
    )
    return await shell(request, "New account", content, active="accounts")


async def create_account(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    form = await form_data(request)
    values = {key: str(form.get(key, ""))[:280] for key in ("discord_user_id", "name", "role", "note")}

    async def operation(conn):
        guild_ids = []
        for value in form.getall("guilds", []):
            try:
                guild_ids.append(positive_id(value))
            except WebError as exc:
                raise AccountError("Choose a server from the list.", "guilds") from exc
        account = await bot.accounts.create(
            actor=request["account"],
            discord_user_id=form.get("discord_user_id", ""),
            name=form.get("name", ""),
            role=form.get("role", ""),
            guild_ids=guild_ids,
            note=form.get("note"),
            conn=conn,
        )
        await record_activity(
            request=request,
            action="account.create",
            target=str(account.id),
            after={"discord_user_id": account.discord_user_id, "name": account.name, "role": account.role, "guilds": sorted(guild_ids)},
            detail={"verified": bool(values.get("discord_user_id"))},
            conn=conn,
        )
        return account

    try:
        account = await mutation(request, operation)
    except AccountError as exc:
        if wants_json(request):
            return web.json_response({"error": "invalid", "message": str(exc), "field": exc.field}, status=400)
        content = new_account_page(guilds=await guild_rows(request), csrf=request["session"].csrf, values=values, errors={exc.field: str(exc)})
        return await shell(request, "New account", content, active="accounts", status=400)
    except WebError as exc:
        if exc.status in (401, 403, 404):
            raise
        if wants_json(request):
            return web.json_response({"error": exc.error, "message": exc.message}, status=exc.status)
        content = new_account_page(guilds=await guild_rows(request), csrf=request["session"].csrf, values=values, errors={"account": exc.message})
        return await shell(request, "New account", content, active="accounts", status=exc.status)
    if wants_json(request):
        return web.json_response({"redirect": f"/dashboard/admin/accounts/{account.id}", "message": "Account created."})
    return web.HTTPSeeOther(f"/dashboard/admin/accounts/{account.id}?saved=created")


async def detail(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    row, sessions = await account_rows(request)
    granted = {guild["id"] for guild in await bot.accounts.guilds(row["id"])}
    activity_rows = await bot.activity_service.query({"account": str(row["id"]), "limit": "20"}, limit=20)
    message = "Account created. Share the sign-in link with them." if request.query.get("saved") else ""
    content = detail_page(
        account=row,
        bot=bot,
        guilds=await guild_rows(request),
        granted=granted,
        csrf=request["session"].csrf,
        sessions=sessions,
        activity_rows=activity_rows,
        message=message,
    )
    return await shell(request, row["name"], content, active="accounts")


async def update_account(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    account_id = positive_id(request.match_info["account_id"])

    async def operation(conn):
        form = await form_data(request)
        before = await bot.db.fetchone_on(conn, "SELECT name, note, role FROM accounts WHERE id = ?", (account_id,))
        if before is None:
            raise WebError(404, "not_found", "That account was not found.")
        target = await bot.accounts.get(account_id, conn=conn)
        if target is None:
            raise WebError(404, "not_found", "That account was not found.")
        role = None if target.is_system else (form.get("role") or target.role)
        result = await bot.accounts.update(
            target.id,
            actor=request["account"],
            name=form.get("name", ""),
            note=form.get("note"),
            role=role,
            conn=conn,
        )
        after = {"name": result.name, "note": result.note, "role": result.role}
        changed = {key: after[key] for key in after if before.get(key) != after[key]}
        if changed:
            await record_activity(
                request=request,
                action="account.update",
                target=str(result.id),
                before={key: before.get(key) for key in changed},
                after=changed,
                conn=conn,
            )
        return result

    try:
        result = await mutation(request, operation)
    except AccountError as exc:
        return await _detail_error(request, exc.field, str(exc))
    if wants_json(request):
        return web.json_response({"redirect": f"/dashboard/admin/accounts/{result.id}?saved=updated", "message": "Account saved."})
    return web.HTTPSeeOther(f"/dashboard/admin/accounts/{result.id}?saved=updated")


async def _detail_error(request: web.Request, field: str, message: str) -> web.Response:
    if wants_json(request):
        return web.json_response({"error": "invalid", "message": message, "field": field}, status=400)
    bot = request.app["bot"]
    row, sessions = await account_rows(request)
    granted = {guild["id"] for guild in await bot.accounts.guilds(row["id"])}
    content = detail_page(
        account=row,
        bot=bot,
        guilds=await guild_rows(request),
        granted=granted,
        csrf=request["session"].csrf,
        sessions=sessions,
        activity_rows=await bot.activity_service.query({"account": str(row["id"]), "limit": "20"}, limit=20),
        errors={field: message},
    )
    return await shell(request, row["name"], content, active="accounts", status=400)


async def set_status(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    account_id = positive_id(request.match_info["account_id"])
    payload: dict[str, Any] = {}

    async def operation(conn):
        form = await form_data(request)
        status = form.get("status", "")
        action = {"suspended": "account.suspend", "active": "account.reactivate", "revoked": "account.revoke"}.get(status)
        if action is None:
            raise AccountError("Choose suspend, reactivate or revoke.", "status")
        target = await bot.accounts.get(account_id, conn=conn)
        if target is None:
            raise WebError(404, "not_found", "That account was not found.")
        before = target.status
        result, deleted = await bot.accounts.set_status(target.id, status, actor=request["account"], conn=conn)
        await record_activity(
            request=request,
            action=action,
            target=str(result.id),
            before={"status": before},
            after={"status": result.status},
            detail={"sessions_deleted": deleted},
            conn=conn,
        )
        payload.update({
            "redirect": f"/dashboard/admin/accounts/{result.id}?saved=status",
            "message": f'Account {result.status}. {deleted} session{"s" if deleted != 1 else ""} deleted.',
        })
        return result

    try:
        await mutation(request, operation)
    except AccountError as exc:
        return await _detail_error(request, exc.field, str(exc))
    if wants_json(request):
        return web.json_response(payload)
    return web.HTTPSeeOther(payload["redirect"])


async def set_grants(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    account_id = positive_id(request.match_info["account_id"])

    async def operation(conn):
        form = await form_data(request)
        selected = set()
        for value in form.getall("guilds", []):
            try:
                selected.add(positive_id(value))
            except WebError as exc:
                raise AccountError("Choose servers from the list.", "guilds") from exc
        target = await bot.accounts.get(account_id, conn=conn)
        if target is None:
            raise WebError(404, "not_found", "That account was not found.")
        added, removed = await bot.accounts.set_grants(target.id, selected, actor=request["account"], conn=conn)
        for guild_id in sorted(added):
            await record_activity(
                request=request, action="account.guild.grant", target=str(target.id),
                guild_id=guild_id, after={"guild_id": guild_id}, conn=conn,
            )
        for guild_id in sorted(removed):
            await record_activity(
                request=request, action="account.guild.revoke", target=str(target.id),
                guild_id=guild_id, before={"guild_id": guild_id}, conn=conn,
            )
        return target.id, added, removed

    try:
        account_id, added, removed = await mutation(request, operation)
    except AccountError as exc:
        return await _detail_error(request, exc.field, str(exc))
    message = f"{len(added)} grant{'s' if len(added) != 1 else ''} added, {len(removed)} removed."
    if wants_json(request):
        return web.json_response({"redirect": f"/dashboard/admin/accounts/{account_id}?saved=grants", "message": message, "added": sorted(added), "removed": sorted(removed)})
    return web.HTTPSeeOther(f"/dashboard/admin/accounts/{account_id}?saved=grants")


def setup_routes(app: web.Application) -> None:
    app.router.add_get("/dashboard/admin/accounts", list_accounts)
    app.router.add_get("/dashboard/admin/accounts/new", new_account)
    app.router.add_post("/dashboard/admin/accounts", create_account)
    app.router.add_get("/dashboard/admin/accounts/{account_id}", detail)
    app.router.add_post("/dashboard/admin/accounts/{account_id}", update_account)
    app.router.add_post("/dashboard/admin/accounts/{account_id}/status", set_status)
    app.router.add_post("/dashboard/admin/accounts/{account_id}/guilds", set_grants)
