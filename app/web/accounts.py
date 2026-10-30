"""Owner-only account lifecycle, grants and the identity lookup convenience."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import io
import logging
import re
import secrets
import time
from typing import Any

import discord
from aiohttp import web

from ..services.accounts import Account, AccountError, validate_discord_id
from ..services.activity import record_activity
from .common import WebError, accessible_guilds, form_data, html, mutation, positive_id, wants_json
from .templates.app.admin_accounts import accounts_page, delete_confirmation_page, detail_page, new_account_page
from .templates.base import layout

log = logging.getLogger(__name__)

CREDENTIAL_DM_TIMEOUT_S = 5.0
CREATED_DM_MESSAGE = "Account created. The bot sent the credentials by DM to the new account's Discord user."
CREATED_DM_WARNING = "Account created, but credentials DM delivery could not be confirmed. Ask the new user to allow DMs or share the credentials confidentially. Do not create another account."
DELETION_CONFIRMATION_TTL_S = 300
DELETION_CHARACTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
DELETION_TOKEN = re.compile(r"([0-9]{1,12})\.([A-HJ-NP-Z2-9]{8})\.([a-f0-9]{64})\Z")


def _deletion_signature(request: web.Request, account_id: int, expires: str, characters: str) -> str:
    payload = f"account.delete:{request['session'].id_hash}:{account_id}:{expires}:{characters}"
    return hmac.new(request.app["account_deletion_key"], payload.encode(), hashlib.sha256).hexdigest()


def _deletion_challenge(request: web.Request, target: Account) -> dict[str, Any]:
    characters = "".join(secrets.choice(DELETION_CHARACTERS) for _ in range(8))
    expires = str(int(time.time()) + DELETION_CONFIRMATION_TTL_S)
    signature = _deletion_signature(request, target.id, expires, characters)
    return {
        "account_id": target.id, "account_name": target.name, "characters": characters,
        "confirmation_token": f"{expires}.{characters}.{signature}",
        "expires_in": DELETION_CONFIRMATION_TTL_S,
    }


def _validate_deletion_confirmation(request: web.Request, account_id: int, form: Any) -> None:
    match = DELETION_TOKEN.fullmatch(form.get("confirmation_token", ""))
    if match is None:
        raise AccountError("Open Delete account to generate new confirmation characters.", "confirmation")
    expires, characters, signature = match.groups()
    expected = _deletion_signature(request, account_id, expires, characters)
    if not hmac.compare_digest(signature, expected) or int(expires) <= int(time.time()):
        raise AccountError("Confirmation expired or is invalid. Open Delete account again for new characters.", "confirmation")
    if not hmac.compare_digest(form.get("confirmation", "").encode(), characters.encode()):
        raise AccountError("The confirmation characters do not match. Enter them exactly as shown.", "confirmation")


async def _deletion_target(request: web.Request, account_id: int, conn: Any) -> Account:
    target = await request.app["bot"].accounts.get(account_id, conn=conn)
    if target is None:
        raise WebError(404, "not_found", "That account was not found.")
    if target.deletion_block_reason:
        raise AccountError(target.deletion_block_reason)
    return target


async def _send_credentials_dm(request: web.Request, account: Account, temporary_password: str) -> bool:
    """Send only after commit, to the new account's Discord identity, never the actor."""
    bot = request.app["bot"]
    recipient_id = account.discord_user_id
    if recipient_id is None or not account.username:
        return False
    loop = asyncio.get_running_loop()
    deadline = request.get("request_deadline", loop.time() + CREDENTIAL_DM_TIMEOUT_S + .1)
    timeout = min(CREDENTIAL_DM_TIMEOUT_S, deadline - loop.time() - .1)
    if timeout <= 0:
        log.warning("credentials DM unconfirmed request=%s account=%s recipient=%s reason=deadline", request["request_id"], account.id, recipient_id)
        return False

    async def send():
        recipient = bot.get_user(recipient_id) or await bot.fetch_user(recipient_id)
        prefix = (
            "Your Appardas console account has been created.\n"
            f"Sign in: {bot.cfg.public_base_url}/login\n"
            f"Username: `{account.username}`\n"
        )
        reminder = "You must change the temporary password on first sign-in before accessing the console. Keep these credentials private and delete this message after changing your password."
        content = prefix + f"Temporary password:\n```\n{temporary_password}\n```\n" + reminder
        # Preserve arbitrary valid passwords without breaking Discord Markdown or its size limit.
        attachment = (
            "```" in temporary_password or not temporary_password.isprintable()
            or temporary_password != temporary_password.strip()
            or len(content.encode("utf-16-le")) // 2 > 2000
        )
        if attachment:
            content = prefix + "Temporary password: copy the entire contents of the attached text file.\n" + reminder
            with io.BytesIO(temporary_password.encode("utf-8")) as stream:
                file = discord.File(stream, filename="temporary-password.txt")
                try:
                    await recipient.send(content, file=file, allowed_mentions=discord.AllowedMentions.none(), suppress_embeds=True)
                finally:
                    file.close()
        else:
            await recipient.send(content, allowed_mentions=discord.AllowedMentions.none(), suppress_embeds=True)

    try:
        await asyncio.wait_for(send(), timeout=timeout)
    except Exception as exc:
        # Discord error bodies can contain submitted message content; never log them.
        log.warning("credentials DM unconfirmed request=%s account=%s recipient=%s reason=%s", request["request_id"], account.id, recipient_id, type(exc).__name__)
        return False
    return True


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
    message = "Account deleted. Its sessions and server grants were removed; activity history was kept." if request.query.get("saved") == "deleted" else ""
    content = accounts_page(rows=rows, bot=bot, query=query, total=len(rows), csrf=request["session"].csrf, message=message)
    return await shell(request, "Accounts", content, active="accounts")


async def new_account(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    values = {key: request.query.get(key, "")[:200] for key in ("discord_user_id", "username", "name", "role", "note")}
    lookup, lookup_error = None, ""
    if values["discord_user_id"]:
        try:
            discord_id = int(validate_discord_id(values["discord_user_id"]))
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
    values = {key: str(form.get(key, ""))[:280] for key in ("discord_user_id", "username", "name", "role", "note")}

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
            username=form.get("username", ""),
            temporary_password=form.get("temporary_password", ""),
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
            after={"discord_user_id": str(account.discord_user_id), "username": account.username, "name": account.name, "role": account.role, "guilds": sorted(guild_ids)},
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
    dm_delivered = await _send_credentials_dm(request, account, form.get("temporary_password", ""))
    location = f'/dashboard/admin/accounts/{account.id}?saved=created&dm={"sent" if dm_delivered else "failed"}'
    message = CREATED_DM_MESSAGE if dm_delivered else CREATED_DM_WARNING
    if wants_json(request):
        return web.json_response({"redirect": location, "message": message, "dm_delivered": dm_delivered})
    return web.HTTPSeeOther(location)


async def detail(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    row, sessions = await account_rows(request)
    granted = {guild["id"] for guild in await bot.accounts.guilds(row["id"])}
    activity_rows = await bot.activity_service.query({"account": str(row["id"]), "limit": "20"}, limit=20)
    message = {
        "created": "Account created. Share the sign-in link, username and temporary password confidentially.",
        "credentials": "Credentials saved. Share the username and temporary password confidentially. Previous sessions were signed out.",
        "updated": "Account saved.",
        "status": "Account status saved.",
        "grants": "Server grants saved.",
    }.get(request.query.get("saved", ""), "")
    if request.query.get("saved") == "created":
        if request.query.get("dm") == "sent":
            message = CREATED_DM_MESSAGE
        elif request.query.get("dm") == "failed":
            message = CREATED_DM_WARNING
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


async def set_credentials(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    account_id = positive_id(request.match_info["account_id"])

    async def operation(conn):
        form = await form_data(request)
        target = await bot.accounts.get(account_id, conn=conn)
        if target is None:
            raise WebError(404, "not_found", "That account was not found.")
        result, deleted = await bot.accounts.set_credentials(
            target.id,
            actor=request["account"],
            username=form.get("username", ""),
            temporary_password=form.get("temporary_password", ""),
            conn=conn,
        )
        await record_activity(
            request=request,
            action="account.credentials.reset",
            target=str(result.id),
            before={"username": target.username},
            after={"username": result.username},
            detail={"sessions_deleted": deleted},
            conn=conn,
        )
        return result

    try:
        result = await mutation(request, operation)
    except AccountError as exc:
        return await _detail_error(request, exc.field, str(exc))
    location = f"/dashboard/admin/accounts/{result.id}?saved=credentials"
    if wants_json(request):
        return web.json_response({"redirect": location, "message": "Credentials saved. Previous sessions were signed out."})
    return web.HTTPSeeOther(location)


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


async def delete_confirmation(request: web.Request) -> web.Response:
    account_id = positive_id(request.match_info["account_id"])

    async def operation(conn):
        target = await _deletion_target(request, account_id, conn)
        return _deletion_challenge(request, target)

    try:
        challenge = await mutation(request, operation)
    except AccountError as exc:
        return await _detail_error(request, exc.field, str(exc))
    if wants_json(request):
        return web.json_response(challenge, headers={"Cache-Control": "no-store"})
    content = delete_confirmation_page(csrf=request["session"].csrf, **challenge)
    return await shell(request, "Delete account", content, active="accounts")


async def delete_account(request: web.Request) -> web.Response:
    bot = request.app["bot"]
    account_id = positive_id(request.match_info["account_id"])

    async def operation(conn):
        target = await _deletion_target(request, account_id, conn)
        form = await form_data(request)
        _validate_deletion_confirmation(request, account_id, form)
        grants = [guild["id"] for guild in await bot.accounts.guilds(account_id, conn=conn)]
        deleted, sessions = await bot.accounts.delete(account_id, actor=request["account"], conn=conn)
        await record_activity(
            request=request, action="account.delete", target=str(deleted.id),
            before={
                "discord_user_id": str(target.discord_user_id) if target.discord_user_id is not None else None,
                "username": target.username, "name": target.name, "role": target.role, "status": target.status,
                "guilds": grants,
            },
            detail={"sessions_deleted": sessions, "grants_deleted": len(grants)}, conn=conn,
        )

    try:
        await mutation(request, operation)
    except AccountError as exc:
        if wants_json(request) or exc.field != "confirmation":
            return await _detail_error(request, exc.field, str(exc))
        row, _ = await account_rows(request)
        content = delete_confirmation_page(
            csrf=request["session"].csrf, error=str(exc),
            **_deletion_challenge(request, Account.from_row(row)),
        )
        return await shell(request, "Delete account", content, active="accounts", status=400)
    location = "/dashboard/admin/accounts?saved=deleted"
    if wants_json(request):
        return web.json_response({"redirect": location, "message": "Account deleted. Activity history was kept."})
    return web.HTTPSeeOther(location)


def setup_routes(app: web.Application) -> None:
    # Short-lived signed challenges need no schema change or unbounded per-session cache.
    # A process restart invalidates open confirmations; account IDs are never reused.
    app["account_deletion_key"] = secrets.token_bytes(32)
    app.router.add_get("/dashboard/admin/accounts", list_accounts)
    app.router.add_get("/dashboard/admin/accounts/new", new_account)
    app.router.add_post("/dashboard/admin/accounts", create_account)
    app.router.add_get("/dashboard/admin/accounts/{account_id}", detail)
    app.router.add_post("/dashboard/admin/accounts/{account_id}", update_account)
    app.router.add_post("/dashboard/admin/accounts/{account_id}/credentials", set_credentials)
    app.router.add_post("/dashboard/admin/accounts/{account_id}/status", set_status)
    app.router.add_post("/dashboard/admin/accounts/{account_id}/guilds", set_grants)
    app.router.add_post("/dashboard/admin/accounts/{account_id}/delete/confirmation", delete_confirmation)
    app.router.add_post("/dashboard/admin/accounts/{account_id}/delete", delete_account)
