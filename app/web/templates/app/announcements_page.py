"""Announcements management page: create, schedule, edit, dispatch, and review history."""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlencode

from ..base import csrf_field, e
from ..pages import notice, select_options, title_row
from ..widgets import stamp


def _parse_payload(payload_raw: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(payload_raw, dict):
        return payload_raw
    try:
        data = json.loads(payload_raw or "{}")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _render_embed_preview(payload: dict[str, Any], channel_name: str) -> str:
    title = payload.get("title", "Announcement")
    body = payload.get("body", "")
    footer = payload.get("footer_text", "")
    image_url = payload.get("image_url", "")
    fields = payload.get("fields") or []
    color = payload.get("color") or "#5865F2"
    ping_role_id = payload.get("ping_role_id")

    ping_badge = ""
    if ping_role_id:
        val = str(ping_role_id).strip()
        label = f"@{val}" if not val.startswith("@") else val
        ping_badge = f'<span class="badge ping-badge" style="background:var(--panel-2);padding:2px 6px;border-radius:var(--r);font-size:12px;color:var(--accent);margin-bottom:8px;display:inline-block;">Mention: {e(label)}</span>'

    fields_html = ""
    if fields and isinstance(fields, list):
        items = []
        for f in fields:
            if isinstance(f, dict):
                fname = f.get("name", "")
                fval = f.get("value", "")
                items.append(f'<div style="margin-top:6px;"><strong style="color:var(--ink);">{e(fname)}</strong><p style="margin:2px 0 0;color:var(--muted);white-space:pre-wrap;">{e(fval)}</p></div>')
        if items:
            fields_html = f'<div class="embed-fields" style="display:grid;gap:8px;margin-top:10px;">{"".join(items)}</div>'

    img_html = ""
    if image_url:
        img_html = f'<div style="margin-top:10px;max-width:400px;border-radius:var(--r);overflow:hidden;"><img src="{e(image_url)}" alt="Preview banner" style="width:100%;max-height:200px;object-fit:cover;display:block;"></div>'

    footer_html = ""
    if footer:
        footer_html = f'<footer style="margin-top:12px;font-size:12px;color:var(--muted);border-top:1px solid var(--line);padding-top:6px;">{e(footer)}</footer>'

    return f'''<div class="embed-preview" style="border-left:4px solid {e(color)};background:var(--panel-2);padding:14px 16px;border-radius:0 var(--r) var(--r) 0;margin-top:10px;">
{ping_badge}
<h4 style="margin:0 0 6px;font-size:15px;color:var(--ink);">{e(title)}</h4>
<p style="margin:0;color:var(--ink);white-space:pre-wrap;line-height:1.4;">{e(body)}</p>
{fields_html}
{img_html}
{footer_html}
</div>'''


def announcements_page(
    *,
    guild: Any,
    pending: list[dict[str, Any]],
    recent: list[dict[str, Any]],
    channels: list[Any],
    roles: list[Any],
    csrf: str,
    writable: bool,
    connected: bool,
    message: str = "",
    error: bool = False,
) -> str:
    channel_choices = [(ch.id, f"#{ch.name}") for ch in channels]
    role_choices = [
        ("", "No ping"),
        ("everyone", "@everyone"),
        ("here", "@here"),
        *[(str(r.id), f"@{r.name}") for r in roles if r.name != "@everyone"],
    ]

    channel_map = {ch.id: f"#{ch.name}" for ch in channels}

    # New announcement form
    disabled = " disabled" if not writable or not connected else ""
    channel_select = f'<select id="channel_id" name="channel_id"{disabled} required>{select_options(channel_choices, "")}</select>'
    ping_select = f'<select id="ping_role_id" name="ping_role_id"{disabled}>{select_options(role_choices, "")}</select>'

    create_panel = f'''<section class="panel" aria-label="Create Announcement">
<div class="panel-head">
  <h2>New Announcement</h2>
  <span class="muted">Live broadcast or scheduled delivery</span>
</div>
<form method="post" action="/dashboard/g/{guild.id}/announcements" class="admin-form" data-enhance>
  {csrf_field(csrf)}
  <div class="field">
    <label for="channel_id">Target Channel</label>
    {channel_select}
  </div>
  <div class="field">
    <label for="title">Announcement Title</label>
    <input id="title" name="title" maxlength="256" placeholder="Important Update" required{disabled}>
  </div>
  <div class="field">
    <label for="body">Message Body (Discord Markdown)</label>
    <textarea id="body" name="body" maxlength="4000" placeholder="Type your announcement content here..." required{disabled}></textarea>
  </div>
  <div class="inline-fields" style="display:grid;grid-template-columns:repeat(auto-fit, minmax(220px, 1fr));gap:16px;">
    <div class="field">
      <label for="when">Schedule (Time Slot)</label>
      <input id="when" name="when" value="now" placeholder="now, in 2h, tomorrow 09:00, or YYYY-MM-DD HH:MM"{disabled}>
      <small>Use <code>now</code> to send immediately, or relative/absolute time.</small>
    </div>
    <div class="field">
      <label for="ping_role_id">Mention / Ping Role</label>
      {ping_select}
      <small>Optional mention included with the broadcast.</small>
    </div>
  </div>
  <details class="panel" style="background:var(--ground);padding:14px;border:1px solid var(--line);border-radius:var(--r);margin-top:8px;">
    <summary style="cursor:pointer;font-weight:500;color:var(--ink);">Embed Customization (Footers, Banner, List Items, Color)</summary>
    <div style="display:grid;gap:14px;margin-top:14px;">
      <div class="inline-fields" style="display:grid;grid-template-columns:repeat(auto-fit, minmax(220px, 1fr));gap:16px;">
        <div class="field">
          <label for="footer_text">Footer Text</label>
          <input id="footer_text" name="footer_text" maxlength="256" placeholder="e.g. Official Server Notice • Staff"{disabled}>
        </div>
        <div class="field">
          <label for="color">Embed Accent Color</label>
          <input id="color" name="color" placeholder="#5865F2" list="color-presets"{disabled}>
          <datalist id="color-presets">
            <option value="#5865F2">Blurple</option>
            <option value="#57F287">Green</option>
            <option value="#FEE75C">Yellow</option>
            <option value="#ED4245">Red</option>
            <option value="#EB459E">Fuchsia</option>
            <option value="#FFFFFF">White</option>
          </datalist>
        </div>
      </div>
      <div class="field">
        <label for="image_url">Image / Banner URL</label>
        <input id="image_url" name="image_url" type="url" placeholder="https://example.com/banner.png"{disabled}>
        <small>Leave empty to use server default announcement banner.</small>
      </div>
      <div class="field">
        <label for="fields_text">List Items / Embed Fields</label>
        <textarea id="fields_text" name="fields_text" placeholder="Title | Content&#10;Feature 1 | High speed performance&#10;Or bullet: - Important rule"{disabled} style="min-height:72px;"></textarea>
        <small>Enter one per line: <code>Title | Value</code> or bullet items starting with <code>- </code>.</small>
      </div>
    </div>
  </details>
  <div class="row-actions" style="display:flex;gap:12px;margin-top:12px;">
    <button class="primary" type="submit" name="action_type" value="send_now"{disabled}>Publish Now</button>
    <button class="plain-button" type="submit" name="action_type" value="schedule"{disabled}>Schedule Announcement</button>
  </div>
  <p class="form-status" role="status" data-form-status></p>
</form>
</section>'''

    # Scheduled queue
    queue_cards = []
    for item in pending:
        payload = _parse_payload(item["payload"])
        ch_name = channel_map.get(int(item["channel_id"]), f"Channel {item['channel_id']}")
        preview_html = _render_embed_preview(payload, ch_name)
        author_name = f"user {item['author_id']}"

        # Fields edit string
        f_list = payload.get("fields") or []
        f_lines = []
        for fl in f_list:
            if isinstance(fl, dict):
                f_lines.append(f"{fl.get('name', '')} | {fl.get('value', '')}")
        f_text_val = "\n".join(f_lines)

        edit_form = f'''<details style="margin-top:12px;background:var(--ground);padding:12px;border:1px solid var(--line);border-radius:var(--r);">
  <summary style="cursor:pointer;color:var(--accent);">Edit announcement #{item['id']}</summary>
  <form method="post" action="/dashboard/g/{guild.id}/announcements/{item['id']}/edit" class="admin-form" style="margin-top:10px;" data-enhance>
    {csrf_field(csrf)}
    <div class="field">
      <label for="edit-title-{item['id']}">Title</label>
      <input id="edit-title-{item['id']}" name="title" value="{e(payload.get('title', ''))}" maxlength="256" required{disabled}>
    </div>
    <div class="field">
      <label for="edit-body-{item['id']}">Body</label>
      <textarea id="edit-body-{item['id']}" name="body" maxlength="4000" required{disabled}>{e(payload.get('body', ''))}</textarea>
    </div>
    <div class="inline-fields" style="display:grid;grid-template-columns:repeat(auto-fit, minmax(200px, 1fr));gap:12px;">
      <div class="field">
        <label for="edit-when-{item['id']}">Scheduled Send Time</label>
        <input id="edit-when-{item['id']}" name="when" value="{e(item.get('send_at', ''))}" required{disabled}>
      </div>
      <div class="field">
        <label for="edit-channel-{item['id']}">Channel</label>
        <select id="edit-channel-{item['id']}" name="channel_id"{disabled}>{select_options(channel_choices, item['channel_id'])}</select>
      </div>
      <div class="field">
        <label for="edit-ping-{item['id']}">Ping</label>
        <select id="edit-ping-{item['id']}" name="ping_role_id"{disabled}>{select_options(role_choices, payload.get('ping_role_id') or "")}</select>
      </div>
    </div>
    <div class="inline-fields" style="display:grid;grid-template-columns:repeat(auto-fit, minmax(200px, 1fr));gap:12px;margin-top:8px;">
      <div class="field">
        <label for="edit-footer-{item['id']}">Footer</label>
        <input id="edit-footer-{item['id']}" name="footer_text" value="{e(payload.get('footer_text', ''))}" maxlength="256"{disabled}>
      </div>
      <div class="field">
        <label for="edit-color-{item['id']}">Color</label>
        <input id="edit-color-{item['id']}" name="color" value="{e(payload.get('color', ''))}" placeholder="#5865F2"{disabled}>
      </div>
      <div class="field">
        <label for="edit-img-{item['id']}">Image URL</label>
        <input id="edit-img-{item['id']}" name="image_url" value="{e(payload.get('image_url', ''))}" type="url"{disabled}>
      </div>
    </div>
    <div class="field" style="margin-top:8px;">
      <label for="edit-fields-{item['id']}">List / Fields</label>
      <textarea id="edit-fields-{item['id']}" name="fields_text"{disabled}>{e(f_text_val)}</textarea>
    </div>
    <div class="row-actions" style="margin-top:10px;">
      <button class="plain-button" type="submit"{disabled}>Save Changes</button>
    </div>
  </form>
</details>''' if writable else ""

        actions_html = f'''<div style="display:flex;gap:8px;margin-top:12px;align-items:center;">
<form method="post" action="/dashboard/g/{guild.id}/announcements/{item['id']}/send" style="display:inline;">
  {csrf_field(csrf)}
  <button class="plain-button" type="submit"{disabled}>Dispatch Now</button>
</form>
<form method="post" action="/dashboard/g/{guild.id}/announcements/{item['id']}/cancel" style="display:inline;">
  {csrf_field(csrf)}
  <button class="plain-button" type="submit" style="color:var(--bad);"{disabled}>Cancel</button>
</form>
</div>''' if writable else ""

        card = f'''<div class="queue-card" style="background:var(--panel);border:1px solid var(--line);border-radius:var(--r);padding:16px;margin-bottom:14px;">
<div style="display:flex;justify-content:space-between;align-items:start;flex-wrap:wrap;gap:10px;">
  <div>
    <strong style="color:var(--ink);">#{item['id']} · {e(ch_name)}</strong>
    <p class="muted" style="margin:2px 0 0;font-size:13px;">Scheduled for: <strong style="color:var(--ink);">{stamp(item['send_at'])}</strong></p>
  </div>
  <span class="badge" style="background:var(--panel-2);padding:4px 8px;border-radius:var(--r);font-size:12px;">{e(item.get('status', 'pending'))}</span>
</div>
{preview_html}
{actions_html}
{edit_form}
</div>'''
        queue_cards.append(card)

    queue_content = "".join(queue_cards) if queue_cards else '<p class="empty">No announcements currently scheduled.</p>'

    queue_panel = f'''<section class="panel" aria-label="Scheduled Queue" style="margin-top:24px;">
<div class="panel-head">
  <h2>Scheduled Queue ({len(pending)})</h2>
  <span class="muted">Announcements awaiting dispatch</span>
</div>
{queue_content}
</section>'''

    # History Table
    recent_rows = []
    for row in recent:
        payload = _parse_payload(row["payload"])
        ch_name = channel_map.get(int(row["channel_id"]), f"Channel {row['channel_id']}")
        status_color = "var(--accent)" if row["status"] == "sent" else ("var(--bad)" if row["status"] == "failed" else "var(--muted)")
        status_badge = f'<span style="color:{status_color};font-weight:500;">{e(row["status"])}</span>'
        recent_rows.append(f'''<tr>
<td>{stamp(row["created_at"])}</td>
<td data-label="Channel">{e(ch_name)}</td>
<td data-label="Title">{e(payload.get("title", "—"))}</td>
<td data-label="Send At">{stamp(row.get("send_at"))}</td>
<td data-label="Status">{status_badge}</td>
<td data-label="Dispatched">{stamp(row.get("sent_at")) if row.get("sent_at") else "—"}</td>
<td data-label="Detail">{e(row.get("error") or "—")}</td>
</tr>''')

    history_tbody = "".join(recent_rows) if recent_rows else '<tr><td colspan="7" class="empty">No past announcements recorded.</td></tr>'

    history_panel = f'''<section class="panel" aria-label="Past Announcements" style="margin-top:24px;">
<div class="panel-head">
  <h2>Announcement History</h2>
  <span class="muted">Past broadcasts and outcomes</span>
</div>
<div class="table-scroll responsive-table">
  <table>
    <thead>
      <tr>
        <th>Created</th>
        <th>Channel</th>
        <th>Title</th>
        <th>Scheduled</th>
        <th>Status</th>
        <th>Dispatched</th>
        <th>Error</th>
      </tr>
    </thead>
    <tbody>
      {history_tbody}
    </tbody>
  </table>
</div>
</section>'''

    return (
        title_row("Announcements", f"{guild.name} · Broadcast immediate notices or schedule rich embeds.")
        + notice(message, error=error)
        + create_panel
        + queue_panel
        + history_panel
    )
