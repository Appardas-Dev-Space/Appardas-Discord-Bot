"""Owner-only accountability log: stable, filterable and exportable."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlencode

from ....services.activity import ACTIVITY_ACTIONS
from ..base import e
from ..pages import select_options, title_row
from ..widgets import json_detail, stamp

GROUPS = (
    ("Auth", tuple(action for action in ACTIVITY_ACTIONS if action.startswith("auth"))),
    ("Accounts", tuple(action for action in ACTIVITY_ACTIONS if action.startswith("account"))),
    ("Guild", ("cog.toggle", "config.set", "authz.denied")),
)


def filters(query: dict[str, str], *, accounts: list[dict[str, Any]], guilds: list[dict[str, Any]]) -> str:
    groups = "".join(
        f'<optgroup label="{e(label)}">' + select_options([(action, action) for action in actions], query.get("action", "")) + "</optgroup>"
        for label, actions in GROUPS
    )
    return (
        '<details class="filter-disclosure" open><summary>Filters</summary><form method="get" class="filter-bar">'
        f'<div class="field wide"><label for="activity-account">Account</label><select id="activity-account" name="account">'
        f'{select_options([("", "All accounts"), ("anonymous", "Anonymous"), *[(row["id"], row["name"] + " · " + row["role"]) for row in accounts]], query.get("account", ""))}</select></div>'
        f'<div class="field wide"><label for="activity-action">Action</label><select id="activity-action" name="action">'
        f'{select_options([("", "All actions")], query.get("action", ""))}{groups}</select></div>'
        f'<div class="field"><label for="activity-outcome">Outcome</label><select id="activity-outcome" name="outcome">'
        f'{select_options([("", "Any"), *[(value, value) for value in ("ok", "denied", "error")]], query.get("outcome", ""))}</select></div>'
        f'<div class="field wide"><label for="activity-guild">Server</label><select id="activity-guild" name="guild">'
        f'{select_options([("", "Any server"), ("global", "No server")], query.get("guild", ""))}'
        f'{select_options([(row["id"], row["name"] or str(row["id"])) for row in guilds], query.get("guild", ""))}</select></div>'
        f'<div class="field"><label for="activity-from">From</label><input id="activity-from" type="date" name="from" value="{e(query.get("from", ""))}"></div>'
        f'<div class="field"><label for="activity-to">To</label><input id="activity-to" type="date" name="to" value="{e(query.get("to", ""))}"></div>'
        f'<div class="field"><label for="activity-limit">Rows</label><select id="activity-limit" name="limit">'
        f'{select_options([(value, value) for value in (50, 100, 200)], query.get("limit", "50"))}</select></div>'
        f'<input type="hidden" name="request_id" value="{e(query.get("request_id", ""))}">'
        '<button class="plain-button" type="submit">Apply</button></form></details>'
    )


def activity_page(*, rows: list[dict[str, Any]], accounts: list[dict[str, Any]], guilds: list[dict[str, Any]], query: dict[str, str], total: int) -> str:
    guild_names = {row["id"]: row["name"] or str(row["id"]) for row in guilds}
    rendered = []
    for row in rows:
        try:
            detail = json.loads(row.get("detail") or "{}")
        except ValueError:
            detail = {}
        label = row.get("account_label") or ("anonymous" if row.get("discord_user_id") is None else f'user {row["discord_user_id"]}')
        change = ""
        if row.get("before_json") or row.get("after_json"):
            change = f'<details><summary>Change</summary><pre><code>{e(row.get("before_json") or "—")} → {e(row.get("after_json") or "—")}</code></pre></details>'
        if detail:
            change += f'<details><summary>Context</summary><pre><code>{json_detail(detail)}</code></pre></details>'
        rendered.append(
            f'<tr class="{"denied" if row["outcome"] == "denied" else ""}">'
            f'<td>{stamp(row["created_at"])}<span class="subline">{e(row["request_id"])}</span></td>'
            f'<td>{e(label)}<span class="subline">{e(row.get("role") or "—")}</span></td>'
            f'<td>{e(row["action"])}</td>'
            f'<td>{e(guild_names.get(row.get("guild_id"), "—") if row.get("guild_id") else "—")}</td>'
            f'<td>{e(row.get("target") or "—")}</td><td>{e(row["outcome"])}</td>'
            f'<td><code>{e(row.get("ip") or "—")}</code></td><td>{change or "—"}</td></tr>'
        )
    older = f'<a href="?{e(urlencode({**{key: value for key, value in query.items() if value and key != "before"}, "before": rows[-1]["id"]}))}">Load older</a>' if len(rows) == int(query.get("limit", "50")) else ""
    empty = '<p class="empty">No activity matches these filters.</p>' if query else '<p class="empty">Nothing logged yet.</p>'
    export = f'/dashboard/admin/activity.csv?{e(urlencode(query))}'
    return title_row("Activity", f"{total} matching rows. Accountable changes and sign-ins; reads are not logged.", action=("Export CSV", export)) + filters(query, accounts=accounts, guilds=guilds) + '<div class="table-scroll responsive-table"><table><thead><tr><th>Time</th><th>Account</th><th>Action</th><th>Server</th><th>Target</th><th>Outcome</th><th>IP</th><th>Details</th></tr></thead><tbody>' + "".join(rendered) + "</tbody></table></div>" + empty + '<nav class="pagination" aria-label="Activity pages"><a href="/dashboard/admin/activity">Latest</a><a href="/dashboard/admin/activity">Refresh</a>' + older + '</nav><p class="muted">IP addresses and browser identifiers are visible only here. They are pruned after the configured retention period.</p>'
