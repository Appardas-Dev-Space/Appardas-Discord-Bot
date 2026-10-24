"""Pure guild screens. Forms and links work before JavaScript runs."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlencode

from ...cogs.registry import COGS
from ...util import human_bytes
from ...version import VERSION
from .base import csrf_field, e
from .widgets import heartbeat_svg, json_detail, readings, renewal_block, stamp

SETTING_LABELS = {"prefix": "Prefix", "announce_channel": "Announcement channel", "github_channel": "GitHub channel", "mod_log": "Moderation log", "timezone": "Timezone"}
VIEWER_NOTE = "Your account can view this server but not change it."


def title_row(title: str, description: str, *, action: tuple[str, str] | None = None) -> str:
    control = f'<a class="primary" href="{e(action[1])}">{e(action[0])}</a>' if action else ''
    return f'<header class="page-title"><div><h1>{e(title)}</h1><p>{e(description)}</p></div><div class="title-actions">{control}</div></header>'


def notice(message: str = "", *, error: bool = False) -> str:
    return f'<p class="form-status{" error" if error else ""}" role="status">{e(message)}</p>' if message else ''


def select_options(choices: list[tuple[Any, str]], selected: Any) -> str:
    return ''.join(f'<option value="{e(value)}"{" selected" if str(value) == str(selected) else ""}>{e(label)}</option>' for value, label in choices)


def _toggle(cog: str, enabled: bool, *, guild_id: int, csrf: str, writable: bool, held: int = 0) -> str:
    visual = '<span class="switch-track" aria-hidden="true"><i></i></span>'
    label = f'Turn {cog} {"off" if enabled else "on"}'
    css = "switch on" if enabled else "switch"
    if COGS[cog].locked or not writable:
        return f'<div class="cog-control"><button type="button" class="{css}" disabled aria-label="{e(cog)} is {"locked" if COGS[cog].locked else "read-only"}">{visual}</button></div>'
    held_note = f'<p>{held} pending announcements will be held until dev is on again.</p>' if cog == "dev" and enabled else ''
    return f'<details class="cog-control toggle-details"><summary class="{css}" aria-label="{e(label)}">{visual}</summary><form class="reason-form" method="post" action="/dashboard/g/{guild_id}/cogs/{cog}" data-enhance>{csrf_field(csrf)}<input type="hidden" name="enabled" value="{0 if enabled else 1}"><label for="reason-{cog}">Reason (optional)</label><input id="reason-{cog}" name="reason" maxlength="500" placeholder="Why change this cog?">{held_note}<p>Saved work is kept. Commands and background work follow this server’s setting.</p><div class="row-actions"><button class="plain-button" type="submit">Turn {"off" if enabled else "on"}</button></div><p class="form-status" role="status" data-form-status></p></form></details>'


def cog_rows(states: dict[str, Any], *, guild_id: int, csrf: str, writable: bool, actors: dict[int, str], held: int = 0, full: bool = False) -> str:
    rows = []
    for key, meta in COGS.items():
        state = states[key]
        detail = meta.description if full else (f"{held} pending announcements" if key == "dev" else meta.description)
        change = f'{e(actors.get(state.updated_by, "system"))} · {stamp(state.updated_at)}' if state.updated_by is not None else 'Never changed. Default is on.'
        reason = f'<p>Reason: {e(state.reason)}</p>' if state.reason else ''
        lock = '<p>This is how you find out something is wrong.</p>' if meta.locked and full else ''
        word = "temporarily unavailable" if state.state == "faulted" else state.state
        rows.append(f'<div class="cog-row" data-cog-row="{key}"><div><h3>{key}{" · locked" if meta.locked else ""}</h3><p>{e(detail)}</p>{reason}{lock}<p data-live-fragment="change-{key}">{change}</p></div><span data-cog-state>{e(word)}</span>{_toggle(key, state.enabled, guild_id=guild_id, csrf=csrf, writable=writable, held=held)}</div>')
    return ''.join(rows)


def audit_mini(rows: list[dict[str, Any]]) -> str:
    return '<div class="audit-mini">' + (''.join(f'<div class="audit-entry">{stamp(row["created_at"])}<p>{e(row["action"])} · {e(row.get("target") or "—")}<span class="muted"> · {e(row.get("actor", "system"))}</span></p></div>' for row in rows) or '<p class="empty">Nothing recorded yet.</p>') + '</div>'


def overview_page(*, guild: Any, snapshot: dict[str, Any], states: dict[str, Any], csrf: str, writable: bool, actors: dict[int, str], held: int, last_renewed: str | None, heartbeat: dict[str, Any], audit_rows: list[dict[str, Any]], message: str = "") -> str:
    title = title_row(guild.name, f'{snapshot["status"]} · since {snapshot.get("started_at", "—")} · appardas {VERSION}', action=("Open in Discord", f"https://discord.com/channels/{guild.id}"))
    status = notice(message) + (notice(VIEWER_NOTE) if not writable else '')
    cog_list = cog_rows(states, guild_id=guild.id, csrf=csrf, writable=writable, actors=actors, held=held)
    return f'{title}{status}<section><h2>Right now</h2>{readings(snapshot)}<p class="muted">Database: {e(human_bytes(snapshot.get("db", {}).get("size_bytes", 0)))} · last write {stamp(snapshot.get("db", {}).get("last_write_at"))}</p></section><section class="panel heartbeat-panel" data-live-fragment="heartbeat"><h2>24-hour heartbeat</h2>{heartbeat_svg(heartbeat)}<p class="heartbeat-caption">{e(heartbeat["summary"])}</p></section><div class="overview-grid"><section class="panel"><div class="panel-head"><h2>Cogs</h2><a href="/dashboard/g/{guild.id}/cogs">Details</a></div>{cog_list}</section><div class="stack">{renewal_block(last_renewed)}<section class="panel" data-live-fragment="recent-audit"><div class="panel-head"><h2>Recent audit</h2><a href="/dashboard/g/{guild.id}/audit">Full log</a></div>{audit_mini(audit_rows)}</section></div></div>'


def cogs_page(*, guild: Any, states: dict[str, Any], csrf: str, writable: bool, actors: dict[int, str], held: int, message: str = "", error: bool = False) -> str:
    return title_row("Cogs", f"{guild.name} · Pausing a cog keeps its saved work.") + notice(message, error=error) + (notice(VIEWER_NOTE) if not writable else '') + '<section class="full-cogs" aria-label="Cog state and controls">' + cog_rows(states, guild_id=guild.id, csrf=csrf, writable=writable, actors=actors, held=held, full=True) + '</section>'


def settings_page(*, guild: Any, values: dict[str, Any], csrf: str, writable: bool, channels: list[Any], connected: bool, message: str = "", errors: dict[str, str] | None = None) -> str:
    errors = errors or {}
    descriptions = {"prefix": "Empty means mention-only. Use 1–16 visible characters without spaces, / or mention patterns.", "announce_channel": "The fixed destination for announcements.", "github_channel": "Default channel for repository updates.", "mod_log": "Where the bot posts moderation and settings notices.", "timezone": "Used when interpreting scheduled times. Choose an IANA timezone."}
    rows = []
    disabled = ' disabled' if not writable else ''
    for key, label in SETTING_LABELS.items():
        value = values.get("mod_log_channel" if key == "mod_log" else key)
        error = errors.get(key, "")
        described = f' aria-describedby="{key}-error"' if error else ''
        if key == "prefix":
            control = f'<input id="setting-{key}" name="value" value="{e(value)}" maxlength="16" data-prefix{disabled}{described}><p class="preview">Preview: <strong data-prefix-preview>{e(str(value) + "help" if value else "@appardas help")}</strong></p>'
        elif key == "timezone":
            choices = ["UTC", "Asia/Manila", "Asia/Tokyo", "Europe/London", "America/New_York", "America/Los_Angeles"]
            control = f'<input id="setting-{key}" name="value" value="{e(value)}" list="timezones" required maxlength="100"{disabled}{described}><datalist id="timezones">' + ''.join(f'<option value="{zone}">' for zone in choices) + '</datalist><p class="muted">Choose a suggestion or type a custom IANA name.</p>'
        else:
            choices = [("", "Not set"), *[(channel.id, "#" + channel.name) for channel in channels]]
            if value and value not in {channel.id for channel in channels}:
                choices.append((value, f"Channel {value} (unavailable)"))
            cold = not connected or not channels
            control = f'<select id="setting-{key}" name="value"{disabled if not cold else " disabled"}{described}>{select_options(choices, value or "")}</select>'
            if cold:
                control += '<p class="muted">Reconnect to Discord to list channels. The value can still be cleared.</p>'
        buttons = '<button class="plain-button" type="submit">Save</button>' if writable and (key in ("prefix", "timezone") or connected and channels) else ''
        if writable and key in ("announce_channel", "github_channel", "mod_log", "prefix"):
            buttons += '<button class="plain-button" name="clear" value="1" type="submit" formnovalidate>Clear</button>'
        rows.append(f'<section class="setting-row"><div class="setting-copy"><h2>{label}</h2><p>{descriptions[key]}</p></div><form method="post" action="/dashboard/g/{guild.id}/settings/{key}" class="setting-form" data-setting="{key}" data-enhance>{csrf_field(csrf)}<label for="setting-{key}">{label}</label>{control}<p class="field-error" id="{key}-error">{e(error)}</p><div class="setting-actions">{buttons}</div><p class="form-status" role="status" data-form-status></p></form></section>')
    return title_row("Settings", f"{guild.name} · Each save changes one setting.") + notice(message) + (notice(VIEWER_NOTE) if not writable else '') + '<div class="settings-list">' + ''.join(rows) + '</div>'


def watches_page(*, guild: Any, watches: list[dict[str, Any]], health: dict[int, str], channels: dict[int, str]) -> str:
    rows = []
    for watch in watches:
        rows.append(f'<tr><td>{e(watch["target"])}</td><td data-label="Type">{e(watch["target_type"])}</td><td data-label="Channel">{e(channels.get(watch["channel_id"], "channel " + str(watch["channel_id"])))}</td><td data-label="Events">{e(watch["events"])}</td><td data-label="Health">{e(health[watch["id"]])}</td><td data-label="Last poll">{stamp(watch.get("last_polled"))}</td><td data-label="Last delivery">{stamp(watch.get("last_delivered"))}</td><td data-label="Error">{e(watch.get("last_error") or "—")}</td></tr>')
    empty = '<p class="empty">No watches. Run <code>/github watch appardas #releases</code>.</p>' if not rows else ''
    return title_row("Watches", f"{guild.name} · Repository updates and their last delivery.") + f'<section class="panel"><h2>GitHub watches</h2><div class="table-scroll responsive-table"><table><thead><tr>{"".join(f"<th>{heading}</th>" for heading in ("Target", "Type", "Channel", "Events", "Health", "Last poll", "Last delivery", "Error"))}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>{empty}<p class="muted">Watches are added and removed in Discord so the channel and events stay explicit. Use <code>/github test &lt;target&gt;</code> to check one.</p></section>'


def audit_page(*, guild: Any, rows: list[dict[str, Any]], actions: list[str], action: str, limit: int, owner: bool) -> str:
    rendered = []
    for row in rows:
        try:
            detail = json.loads(row.get("detail") or "{}")
        except ValueError:
            detail = {}
        if not isinstance(detail, dict):
            detail = {}
        sibling = f'<a href="/dashboard/admin/activity?request_id={e(detail["request_id"])}">Accountable record</a>' if owner and detail.get("request_id") else ''
        rendered.append(f'<tr id="audit-{row["id"]}"><td>{stamp(row["created_at"])}</td><td data-label="Actor">{e(row.get("actor", "system"))}</td><td data-label="Action">{e(row["action"])}</td><td data-label="Target">{e(row.get("target") or "—")}</td><td data-label="Source">{e(detail.get("source", "bot"))}</td><td><details><summary>Details</summary><pre><code>{json_detail(detail)}</code></pre>{sibling}</details></td></tr>')
    filters = f'<form method="get" class="filter-bar"><div class="field"><label for="audit-action">Action</label><select id="audit-action" name="action">{select_options([("", "All actions"), *[(value, value) for value in actions]], action)}</select></div><div class="field"><label for="audit-limit">Rows</label><select id="audit-limit" name="limit">{select_options([(value, str(value)) for value in (50, 100, 200)], limit)}</select></div><button class="plain-button" type="submit">Apply filters</button></form>'
    older = f'<a href="?{e(urlencode({"action": action, "limit": limit, "before": rows[-1]["id"]}))}">Load older</a>' if len(rows) == limit else ''
    return title_row("Audit", f"{guild.name} · The server-facing record of changes.") + filters + f'<div class="table-scroll responsive-table"><table><thead><tr><th>Time</th><th>Actor</th><th>Action</th><th>Target</th><th>Source</th><th>Detail</th></tr></thead><tbody>{"".join(rendered)}</tbody></table></div>' + ('<p class="empty">Nothing recorded yet.</p>' if not rows else '') + f'<nav class="pagination" aria-label="Audit pages"><a href="/dashboard/g/{guild.id}/audit">Latest</a>{older}</nav>'
