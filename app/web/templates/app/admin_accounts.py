"""Closed-registration account administration, rendered as forms first."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from ....services.accounts import Account
from ..base import csrf_field, e
from ..pages import notice, select_options, title_row
from ..widgets import stamp

ROLES = ("admin", "viewer")
STATUSES = ("active", "suspended", "revoked")


def role_word(role: str) -> str:
    return f'<span class="role">{e(role)}</span>'


def status_word(status: str) -> str:
    return f'<span class="status {e(status)}"><i aria-hidden="true"></i>{e(status)}</span>'


def handle(bot: Any, discord_user_id: int | str | None) -> str:
    if discord_user_id is None:
        return "Local account"
    user = bot.get_user(int(discord_user_id))
    return "@" + user.name if user is not None else f"user {discord_user_id}"


def delete_account_button(account: dict[str, Any], csrf: str) -> str:
    return (
        f'<form method="post" action="/dashboard/admin/accounts/{account["id"]}/delete/confirmation" '
        f'class="delete-account-launch" data-delete-account="delete-account-modal" data-account-name="{e(account["name"])}">{csrf_field(csrf)}'
        '<button class="danger-button" type="submit">Delete account</button></form>'
    )


def _deletion_form(*, csrf: str, account_id: int | None = None, account_name: str = "this account", characters: str = "", confirmation_token: str = "", modal: bool = False, error: str = "") -> str:
    action = f"/dashboard/admin/accounts/{account_id}/delete" if account_id is not None else ""
    cancel = (
        '<button class="plain-button" type="button" data-delete-cancel>Cancel</button>' if modal else
        f'<a href="/dashboard/admin/accounts/{account_id}">Cancel</a>'
    )
    heading = '<h2 id="delete-account-title">Delete account</h2>' if modal else ''
    return (
        f'<form method="post" action="{action}" class="setting-form" data-enhance data-delete-confirmation>{csrf_field(csrf)}'
        f'<input type="hidden" name="confirmation_token" value="{e(confirmation_token)}">{heading}'
        f'<p id="delete-account-description">To delete <strong data-delete-account-name>{e(account_name)}</strong>, '
        'enter the random characters below and press Confirm deletion.</p>'
        '<p class="muted">This permanently removes sign-in credentials, active sessions and server grants. '
        'Activity history is kept. This cannot be undone.</p>'
        f'<p class="deletion-characters"><code data-confirmation-characters>{e(characters)}</code></p>'
        '<div class="field"><label for="delete-confirmation">Confirmation characters</label>'
        f'<input id="delete-confirmation" name="confirmation" required minlength="8" maxlength="8" '
        f'pattern="{e(characters or "[A-HJ-NP-Z2-9]{8}")}" autocomplete="off" autocapitalize="none" spellcheck="false" '
        f'aria-describedby="delete-confirmation-hint"{" disabled" if modal else ""}>'
        '<small id="delete-confirmation-hint">Enter the characters exactly as shown. They expire after 5 minutes.</small></div>'
        f'<p class="form-status{" error" if error else ""}" role="status" data-form-status>{e(error)}</p>'
        f'<div class="setting-actions deletion-actions">{cancel}'
        f'<button class="danger-button" type="submit" data-confirm-delete{" disabled" if modal else ""}>Confirm deletion</button>'
        '</div></form>'
    )


def delete_account_dialog(csrf: str) -> str:
    return (
        '<dialog id="delete-account-modal" class="account-delete-dialog" aria-labelledby="delete-account-title" '
        'aria-describedby="delete-account-description">'
        + _deletion_form(csrf=csrf, modal=True) + '</dialog>'
    )


def delete_confirmation_page(*, csrf: str, account_id: int, account_name: str, characters: str, confirmation_token: str, error: str = "", **_: Any) -> str:
    return title_row("Delete account", account_name) + '<section class="panel">' + _deletion_form(
        csrf=csrf, account_id=account_id, account_name=account_name, characters=characters,
        confirmation_token=confirmation_token, error=error,
    ) + '</section>'


def accounts_page(*, rows: list[dict[str, Any]], bot: Any, query: dict[str, str], total: int, csrf: str, message: str = "") -> str:
    rendered = "".join(
        f'<tr><td><a href="/dashboard/admin/accounts/{row["id"]}">{e(row["name"])}</a>'
        f'<span class="subline">{e(row.get("username") or "Credentials not assigned")} · {e(handle(bot, row["discord_user_id"]))}</span>'
        f'<span class="tablet-account-meta">Last sign-in: {stamp(row["last_login_at"])} · Created by: '
        f'{e(row["creator_name"] or ("BOT_OWNER_IDS" if row["is_system"] else "—"))}</span></td>'
        f'<td data-label="Role">{role_word(row["role"])}</td>'
        f'<td data-label="Status">{status_word(row["status"])}</td>'
        f'<td data-label="Servers" class="num">{row["guild_count"]}</td>'
        f'<td data-label="Last sign-in">{stamp(row["last_login_at"])}</td>'
        f'<td data-label="Created by">{e(row["creator_name"] or ("BOT_OWNER_IDS" if row["is_system"] else "—"))}</td>'
        f'<td><div class="account-row-actions"><a href="/dashboard/admin/accounts/{row["id"]}">Open</a>'
        f'{delete_account_button(row, csrf) if not Account.from_row(row).deletion_block_reason else ""}</div></td></tr>'
        for row in rows
    )
    filters = (
        '<form method="get" class="filter-bar">'
        f'<div class="field wide"><label for="account-q">Name, username or Discord ID</label>'
        f'<input id="account-q" name="q" value="{e(query.get("q", ""))}" maxlength="200"></div>'
        f'<div class="field"><label for="account-role">Role</label><select id="account-role" name="role">'
        f'{select_options([("", "All roles"), *[(role, role) for role in ("owner", *ROLES)]], query.get("role", ""))}</select></div>'
        f'<div class="field"><label for="account-status">Status</label><select id="account-status" name="status">'
        f'{select_options([("", "All statuses"), *[(status, status) for status in STATUSES]], query.get("status", ""))}</select></div>'
        '<button class="plain-button" type="submit">Filter</button></form>'
    )
    empty = '<p class="empty">No accounts match these filters. The owner creates local sign-in credentials here.</p>' if not rows else ""
    return title_row("Accounts", f"{total} accounts. Access is assigned here, never self-registered.", action=("New account", "/dashboard/admin/accounts/new")) + notice(message) + filters + '<div class="table-scroll responsive-table accounts-table"><table><thead><tr><th>Name</th><th>Role</th><th>Status</th><th class="num">Servers</th><th>Last sign-in</th><th>Created by</th><th></th></tr></thead><tbody>' + rendered + '</tbody></table></div>' + empty + delete_account_dialog(csrf)


def guild_choices(guilds: list[dict[str, Any]], selected: set[int], *, csrf: str, writable: bool = True) -> str:
    rows = []
    for guild in guilds:
        left = guild.get("left_at")
        checked = " checked" if guild["id"] in selected else ""
        disabled = " disabled" if left or not writable else ""
        note = '<span class="muted"> · appardas left this server</span>' if left else ""
        rows.append(f'<label class="grant-label"><input type="checkbox" name="guilds" value="{guild["id"]}"{checked}{disabled}>{e(guild["name"] or guild["id"])}{note}</label>')
    return "".join(rows) or '<p class="muted">The bot is in no servers yet.</p>'


def new_account_page(*, guilds: list[dict[str, Any]], csrf: str, values: dict[str, str], lookup: dict[str, Any] | None = None, errors: dict[str, str] | None = None, lookup_error: str = "") -> str:
    errors = errors or {}
    result = ""
    if lookup:
        result = f'<div class="lookup-result"><p><strong>{e(lookup.get("username", ""))}</strong> · Discord ID {e(values.get("discord_user_id", ""))} · joined {e(str(lookup.get("created_at", ""))[:10] or "unknown")}</p></div>'
    if lookup_error:
        result = f'<p class="field-error" role="status">{e(lookup_error)}</p>'
    look_up = f'/dashboard/admin/accounts/new?{e(urlencode({"discord_user_id": values.get("discord_user_id", "")}))}'
    return title_row("New account", "Only the owner can create access. There is no sign-up form.") + (
        f'<form method="post" action="/dashboard/admin/accounts" class="admin-form panel" data-enhance>{csrf_field(csrf)}'
        f'<p class="field-error">{e(errors.get("account", ""))}</p>'
        f'<div class="field"><label for="discord-id">Discord user ID</label>'
        f'<input id="discord-id" name="discord_user_id" value="{e(values.get("discord_user_id", ""))}" required inputmode="numeric" pattern="[1-9][0-9]{{16,19}}" maxlength="20">'
        f'<small>A 17–20 digit snowflake. <a href="{look_up}">Look up this ID</a> to prefill the name.</small>'
        f'<p class="field-error">{e(errors.get("discord_user_id", ""))}</p>{result}</div>'
        f'<div class="field"><label for="account-username">Username</label>'
        f'<input id="account-username" name="username" value="{e(values.get("username", ""))}" required maxlength="64" pattern="[A-Za-z0-9._-]{{1,64}}" autocomplete="off" autocapitalize="none">'
        '<small>Local sign-in name. Case-insensitive; letters, digits, dots, underscores and hyphens only.</small>'
        f'<p class="field-error">{e(errors.get("username", ""))}</p></div>'
        '<div class="field"><label for="temporary-password">Temporary password</label>'
        '<input type="password" id="temporary-password" name="temporary_password" required minlength="6" autocomplete="new-password">'
        '<small>At least 6 characters. Share confidentially; they must change it before accessing the console.</small>'
        f'<p class="field-error">{e(errors.get("temporary_password", ""))}</p></div>'
        f'<div class="field"><label for="account-name">Display name</label>'
        f'<input id="account-name" name="name" value="{e(values.get("name", ""))}" required maxlength="64">'
        f'<p class="field-error">{e(errors.get("name", ""))}</p></div>'
        f'<div class="field"><label for="new-role">Role</label><select id="new-role" name="role">'
        f'{select_options([(role, role) for role in ROLES], values.get("role", "viewer"))}</select>'
        '<small>Owners are managed by <code>BOT_OWNER_IDS</code> and cannot be created here.</small></div>'
        f'<fieldset><legend>Server grants</legend><div class="grants">{guild_choices(guilds, set(), csrf=csrf)}</div>'
        '<small>Zero grants is allowed. They can sign in, but will see no servers.</small>'
        f'<p class="field-error">{e(errors.get("guilds", ""))}</p></fieldset>'
        f'<div class="field"><label for="account-note">Note</label><textarea id="account-note" name="note" maxlength="280">{e(values.get("note", ""))}</textarea>'
        '<small>Who is this, and why do they have access? Owner-only.</small></div>'
        '<div class="setting-actions"><button class="primary" type="submit">Create account</button>'
        '<a href="/dashboard/admin/accounts">Cancel</a></div>'
        '<p class="form-status" role="status" data-form-status></p></form>'
    )


def _activity_rows(rows: list[dict[str, Any]]) -> str:
    rendered = []
    for row in rows:
        change = "—"
        if row.get("before_json") or row.get("after_json"):
            change = f'<details><summary>Change</summary><pre><code>{e(row.get("before_json") or "—")} → {e(row.get("after_json") or "—")}</code></pre></details>'
        rendered.append(f'<tr><td>{stamp(row["created_at"])}</td><td>{e(row["action"])}</td><td>{e(row["outcome"])}</td><td>{change}</td></tr>')
    return "".join(rendered)


def detail_page(*, account: dict[str, Any], bot: Any, guilds: list[dict[str, Any]], granted: set[int], csrf: str, sessions: int, activity_rows: list[dict[str, Any]], message: str = "", errors: dict[str, str] | None = None, selected: set[int] | None = None) -> str:
    errors = errors or {}
    system = bool(account["is_system"])
    association = f'Discord ID {account["discord_user_id"]}' if account["discord_user_id"] is not None else "Local credentials only"
    title = title_row(account["name"], f'{handle(bot, account["discord_user_id"])} · {association}')
    login_ip = f' from <code>{e(account["last_login_ip"])}</code>' if account.get("last_login_ip") else ""
    discord_copy = f' <button type="button" class="copy-button" data-copy="{e(account["discord_user_id"])}">Copy</button>' if account["discord_user_id"] is not None else ""
    identity = (
        '<section class="panel"><h2>Identity</h2><dl class="detail-list">'
        f'<dt>Username</dt><dd><code>{e(account.get("username") or "Not assigned")}</code></dd>'
        f'<dt>Role</dt><dd>{role_word(account["role"])}{" · managed by BOT_OWNER_IDS" if system else ""}</dd>'
        f'<dt>Status</dt><dd>{status_word(account["status"])}</dd>'
        f'<dt>Discord</dt><dd><code>{e(account["discord_user_id"] if account["discord_user_id"] is not None else "—")}</code>{discord_copy}</dd>'
        f'<dt>Note</dt><dd>{e(account["note"] or "—")}</dd><dt>Created</dt><dd>{stamp(account["created_at"])}</dd>'
        f'<dt>Last sign-in</dt><dd>{stamp(account["last_login_at"])}{login_ip}</dd>'
        f'<dt>Active sessions</dt><dd>{sessions}</dd></dl>'
        f'<p class="muted">Share <code>{e(bot.cfg.public_base_url)}/login</code>, their username and temporary password confidentially. They sign in with username and password, not Discord.</p></section>'
    )
    credentials = (
        f'<form method="post" action="/dashboard/admin/accounts/{account["id"]}/credentials" class="setting-form panel" data-enhance>{csrf_field(csrf)}'
        f'<h2>{"Reset credentials" if account.get("username") else "Assign credentials"}</h2>'
        '<p class="muted">Assign local credentials to this existing account without changing its Discord ID, role, status or server grants. Saving signs out previous sessions and requires a password change at next sign-in.</p>'
        '<div class="field"><label for="credentials-username">Username</label>'
        f'<input id="credentials-username" name="username" value="{e(account.get("username") or "")}" required maxlength="64" pattern="[A-Za-z0-9._-]{{1,64}}" autocomplete="off" autocapitalize="none"{" readonly" if system and account.get("username") else ""}>'
        '<small>Case-insensitive; letters, digits, dots, underscores and hyphens only. Existing system usernames cannot be renamed.</small>'
        f'<p class="field-error">{e(errors.get("username", ""))}</p></div>'
        '<div class="field"><label for="credentials-password">Temporary password</label>'
        '<input type="password" id="credentials-password" name="temporary_password" required minlength="6" autocomplete="new-password">'
        f'<p class="field-error">{e(errors.get("temporary_password", ""))}</p></div>'
        f'<p class="field-error">{e(errors.get("account", ""))}</p>'
        '<div class="setting-actions"><button class="plain-button" type="submit">Save credentials</button></div>'
        '<p class="form-status" role="status" data-form-status></p></form>'
    )
    if system:
        role_form = '<section class="panel"><h2>Role and label</h2><p class="muted">This owner is managed by <code>BOT_OWNER_IDS</code>. It cannot be suspended, revoked or demoted here.</p></section>'
    else:
        role_form = (
            f'<form method="post" action="/dashboard/admin/accounts/{account["id"]}" class="setting-form panel" data-enhance>{csrf_field(csrf)}'
            '<h2>Role and label</h2>'
            f'<div class="field"><label for="edit-name">Display name</label><input id="edit-name" name="name" value="{e(account["name"])}" required maxlength="64"><p class="field-error">{e(errors.get("name", ""))}</p></div>'
            f'<div class="field"><label for="edit-note">Note</label><textarea id="edit-note" name="note" maxlength="280">{e(account["note"] or "")}</textarea></div>'
            '<fieldset><legend>Role</legend>'
            f'<label class="grant-label"><input type="radio" name="role" value="viewer"{" checked" if account["role"] == "viewer" else ""}> Viewer — read only</label>'
            f'<label class="grant-label"><input type="radio" name="role" value="admin"{" checked" if account["role"] == "admin" else ""}> Admin — change cogs and settings</label>'
            f'</fieldset><p class="field-error">{e(errors.get("role", ""))}</p>'
            '<div class="setting-actions"><button class="plain-button" type="submit">Save role and label</button></div>'
            '<p class="form-status" role="status" data-form-status></p></form>'
        )
    chosen = selected if selected is not None else granted
    grants = []
    for guild in guilds:
        left = guild.get("left_at")
        grants.append(f'<label class="grant-label" data-filter-text="{e(guild["name"] or guild["id"])}"><input type="checkbox" name="guilds" value="{guild["id"]}"{" checked" if guild["id"] in chosen else ""}{" disabled" if left else ""}>{e(guild["name"] or guild["id"])}{" · appardas left this server" if left else ""}</label>')
    access = (
        f'<form method="post" action="/dashboard/admin/accounts/{account["id"]}/guilds" class="setting-form panel" data-enhance>{csrf_field(csrf)}'
        '<h2>Access</h2><div class="field"><label for="grant-filter">Filter servers</label>'
        '<input id="grant-filter" data-filter-list="grant-list" placeholder="Server name" autocomplete="off"></div>'
        f'<div class="grants" id="grant-list">{"".join(grants)}</div>'
        f'<p class="field-error">{e(errors.get("guilds", ""))}</p>'
        '<div class="setting-actions"><button class="plain-button" type="submit">Save grants</button></div>'
        '<p class="form-status" role="status" data-form-status></p></form>'
    )
    danger = ""
    if not system:
        actions = []
        if account["status"] == "active":
            actions.append(("suspend", "Suspend", "Deletes every active session. Sign-in is denied until reactivated."))
        else:
            actions.append(("active", "Reactivate", "Allows sign-in again for the existing grants."))
        if account["status"] != "revoked":
            actions.append(("revoked", "Revoke", "Deletes every active session and keeps the row for the record."))
        rendered = "".join(
            f'<details><summary>{label}</summary><form method="post" action="/dashboard/admin/accounts/{account["id"]}/status" data-enhance>{csrf_field(csrf)}'
            f'<input type="hidden" name="status" value="{value}"><p>{e(explanation)}</p>'
            f'<button class="danger-button" type="submit">{label}</button>'
            '<p class="form-status" role="status" data-form-status></p></form></details>'
            for value, label, explanation in actions
        )
        danger = f'<section class="panel"><h2>Danger</h2><p class="muted">{sessions} active session{"s" if sessions != 1 else ""}. These actions apply on the next request.</p><div class="danger-actions">{rendered}</div></section>'
    recent = _activity_rows(activity_rows)
    empty_activity = '<p class="empty">Nothing logged for this account yet.</p>' if not recent else ""
    activity = (
        '<section class="panel"><div class="panel-head"><h2>Recent activity</h2>'
        f'<a href="/dashboard/admin/activity?{e(urlencode({"account": account["id"]}))}">Filtered view</a></div>'
        '<div class="table-scroll responsive-table"><table><thead><tr><th>Time</th><th>Action</th><th>Result</th><th>Change</th></tr></thead>'
        f'<tbody>{recent}</tbody></table></div>{empty_activity}</section>'
    )
    blocked = Account.from_row(account).deletion_block_reason
    deletion = '<section class="panel"><h2>Delete account</h2>'
    if blocked:
        deletion += f'<p class="muted">{e(blocked)}</p></section>'
    else:
        deletion += (
            '<p class="muted">Permanently remove this account, its sign-in credentials, sessions and server grants. '
            'Activity history is kept. This cannot be undone.</p>'
            f'<p class="field-error">{e(errors.get("confirmation", ""))}</p>'
            f'<div class="setting-actions">{delete_account_button(account, csrf)}</div></section>'
            + delete_account_dialog(csrf)
        )
    return title + notice(message) + '<div class="stack">' + identity + credentials + role_form + access + danger + activity + deletion + '</div>'
