"""Command execution audit log: track time slots, actors, commands, arguments, and outcomes."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlencode

from ..base import e
from ..pages import select_options, title_row
from ..widgets import json_detail, stamp


def commands_page(
    *,
    guild: Any,
    rows: list[dict[str, Any]],
    distinct_commands: list[str],
    command: str,
    user_id: int | None,
    status: str,
    limit: int,
    channels: dict[int, str],
) -> str:
    rendered = []
    for row in rows:
        try:
            opts = json.loads(row.get("options") or "{}")
        except ValueError:
            opts = row.get("options") or {}

        st = row.get("status", "success")
        st_color = "var(--accent)" if st == "success" else ("var(--bad)" if st in ("error", "denied") else "var(--muted)")
        badge = f'<span style="color:{st_color};font-weight:500;">{e(st)}</span>'

        channel_id = row.get("channel_id")
        ch_str = channels.get(int(channel_id), f"channel {channel_id}") if channel_id else "DM / Direct"

        detail_opts = ""
        if opts:
            detail_opts = f'<details><summary>Options</summary><pre><code>{json_detail(opts) if isinstance(opts, (dict, list)) else e(str(opts))}</code></pre></details>'
        else:
            detail_opts = '<span class="muted">—</span>'

        user_display = f"{row.get('user_name', 'unknown')} <span class=\"muted\">({row.get('user_id')})</span>"
        cmd_display = f"/{row.get('command', 'unknown')}"

        row_class = "denied" if st in ("denied", "error") else ""
        rendered.append(f'''<tr class="{row_class}" id="cmd-{row['id']}">
<td>{stamp(row["created_at"])}</td>
<td data-label="User">{user_display}</td>
<td data-label="Command"><code>{e(cmd_display)}</code></td>
<td data-label="Channel">{e(ch_str)}</td>
<td data-label="Outcome">{badge}</td>
<td data-label="Details">{detail_opts}</td>
</tr>''')

    cmd_choices = [("", "All commands"), *[(c, f"/{c}") for c in distinct_commands]]
    status_choices = [("", "All outcomes"), ("success", "Success"), ("error", "Error"), ("denied", "Denied")]
    limit_choices = [(str(n), str(n)) for n in (50, 100, 200)]

    filters = f'''<form method="get" class="filter-bar">
<div class="field">
  <label for="filter-command">Command</label>
  <select id="filter-command" name="command">{select_options(cmd_choices, command)}</select>
</div>
<div class="field">
  <label for="filter-user">User ID</label>
  <input id="filter-user" name="user_id" value="{e(user_id or '')}" placeholder="e.g. 123456789">
</div>
<div class="field">
  <label for="filter-status">Outcome</label>
  <select id="filter-status" name="status">{select_options(status_choices, status)}</select>
</div>
<div class="field">
  <label for="filter-limit">Rows</label>
  <select id="filter-limit" name="limit">{select_options(limit_choices, str(limit))}</select>
</div>
<button class="plain-button" type="submit">Apply filters</button>
</form>'''

    query_params: dict[str, Any] = {"limit": limit}
    if command:
        query_params["command"] = command
    if user_id:
        query_params["user_id"] = user_id
    if status:
        query_params["status"] = status
    if rows and len(rows) == limit:
        query_params["before"] = rows[-1]["id"]
        older = f'<a href="?{e(urlencode(query_params))}">Load older</a>'
    else:
        older = ""

    pagination = f'<nav class="pagination" aria-label="Command log pages"><a href="/dashboard/g/{guild.id}/commands">Latest</a>{older}</nav>'

    content = f'''{filters}
<div class="table-scroll responsive-table">
  <table>
    <thead>
      <tr>
        <th>Time Slot</th>
        <th>User</th>
        <th>Command</th>
        <th>Channel</th>
        <th>Outcome</th>
        <th>Options</th>
      </tr>
    </thead>
    <tbody>
      {"".join(rendered) if rendered else '<tr><td colspan="6" class="empty">No commands recorded for this filter.</td></tr>'}
    </tbody>
  </table>
</div>
{pagination}'''

    return (
        title_row("Command Log", f"{guild.name} · Audit trail of command executions with time slot and user.")
        + content
    )
