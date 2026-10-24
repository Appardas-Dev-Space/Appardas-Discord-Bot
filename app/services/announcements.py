"""Announcements: create, schedule, parse times, dispatch, cancel.

The fixed announcement channel is a feature, not a limitation: one target,
predictable formatting, nowhere to leak a post by accident.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

import discord

import io
from ..config import Settings
from ..db import Database
from ..util import iso, truncate, utcnow
from ..views.embeds import announcement_embed
from .audit import AuditService
from .images import get_default_announcement_image

log = logging.getLogger(__name__)

_WEEKDAY_NAMES = {
    "mon": 0, "monday": 0,
    "tue": 1, "tuesday": 1,
    "wed": 2, "wednesday": 2,
    "thu": 3, "thursday": 3,
    "fri": 4, "friday": 4,
    "sat": 5, "saturday": 5,
    "sun": 6, "sunday": 6,
}
_RELATIVE_RE = re.compile(
    r"^in\s+(?P<amount>\d+)\s*(?P<unit>m|min|mins|minute|minutes|h|hr|hrs|hour|hours|d|day|days)$"
)
_CLOCK = r"(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>am|pm)?"


class AnnouncementError(ValueError):
    pass


class AnnouncementLimitError(AnnouncementError):
    pass


def parse_when(text: str, tz: ZoneInfo, now: datetime | None = None) -> datetime:
    """Parse the documented scheduling syntax in the guild's timezone.

    Accepts `in 2h`, `in 30m`, `in 3d`, `tomorrow 09:00`, `today 18:00`,
    `mon 09:00`, `monday 09:00`, `2026-11-01 18:00`.
    """
    raw = (text or "").strip().lower()
    now_utc = now or utcnow()
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=ZoneInfo("UTC"))
    local_now = now_utc.astimezone(tz)
    if not raw:
        raise AnnouncementError("Give a time, for example `in 2h`, `tomorrow 09:00` or `mon 09:00`.")

    match = _RELATIVE_RE.match(raw)
    if match:
        amount = int(match.group("amount"))
        unit = match.group("unit")
        if unit.startswith("d"):
            delta = timedelta(days=amount)
        elif unit.startswith("h"):
            delta = timedelta(hours=amount)
        else:
            delta = timedelta(minutes=amount)
        if amount <= 0:
            raise AnnouncementError("The relative time must be positive.")
        return (now_utc + delta).astimezone(ZoneInfo("UTC"))

    def _clock(text_value: str) -> time:
        found = re.fullmatch(_CLOCK, text_value.strip())
        if not found:
            raise AnnouncementError(
                f"`{text_value}` is not a clock time. Use `09:00`, `9:00`, `9am` or `6pm`."
            )
        hour = int(found.group("hour"))
        minute = int(found.group("minute") or 0)
        ampm = found.group("ampm")
        if ampm == "pm" and hour < 12:
            hour += 12
        if ampm == "am" and hour == 12:
            hour = 0
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise AnnouncementError(f"`{text_value}` is not a valid time of day.")
        return time(hour=hour, minute=minute)

    if raw.startswith("tomorrow") or raw.startswith("today"):
        relative_day = 1 if raw.startswith("tomorrow") else 0
        rest = raw.split(" ", 1)[1] if " " in raw else ""
        clock = _clock(rest) if rest else time(9, 0)
        target_date = local_now.date() + timedelta(days=relative_day)
        if relative_day == 0 and clock <= local_now.time():
            raise AnnouncementError("That time has already passed today. Use `tomorrow` or a clock time later today.")
        return datetime.combine(target_date, clock, tzinfo=tz).astimezone(ZoneInfo("UTC"))

    first = raw.split(" ", 1)[0]
    if first in _WEEKDAY_NAMES:
        rest = raw.split(" ", 1)[1] if " " in raw else ""
        clock = _clock(rest) if rest else time(9, 0)
        target_weekday = _WEEKDAY_NAMES[first]
        days_ahead = (target_weekday - local_now.weekday()) % 7
        candidate = datetime.combine(
            local_now.date() + timedelta(days=days_ahead), clock, tzinfo=tz
        )
        if candidate <= local_now:
            candidate += timedelta(days=7)
        return candidate.astimezone(ZoneInfo("UTC"))

    # Absolute: 2026-11-01 18:00 / 2026-11-01T18:00
    absolute_match = re.fullmatch(r"(\d{4}-\d{2}-\d{2})[ t](\d{1,2}:\d{2})", raw)
    if absolute_match:
        try:
            day = date.fromisoformat(absolute_match.group(1))
        except ValueError:
            raise AnnouncementError(
                f"`{absolute_match.group(1)}` is not a real calendar date."
            ) from None
        clock = _clock(absolute_match.group(2))
        candidate = datetime.combine(day, clock, tzinfo=tz)
        if candidate <= local_now:
            raise AnnouncementError("That date and time are in the past.")
        return candidate.astimezone(ZoneInfo("UTC"))

    raise AnnouncementError(
        "Could not read that time. Use `in 2h`, `tomorrow 09:00`, `mon 09:00` "
        "or `2026-11-01 18:00`."
    )


def local_text(dt_utc: datetime, tz: ZoneInfo) -> str:
    local = dt_utc.astimezone(tz)
    return local.strftime("%a %d %b %Y, %H:%M %Z")


@dataclass(frozen=True, slots=True)
class DispatchResult:
    sent: int
    failed: int
    skipped: int


class AnnouncementService:
    def __init__(self, db: Database, config: Settings, audit: AuditService) -> None:
        self.db = db
        self.config = config
        self.audit = audit

    async def pending_count(self, guild_id: int, *, conn: sqlite3.Connection | None = None) -> int:
        sql = "SELECT COUNT(*) as cnt FROM announcements WHERE guild_id = ? AND status = 'pending'"
        params = (guild_id,)
        if conn is not None:
            row = await self.db.fetchone_on(conn, sql, params)
            return int(row["cnt"]) if row else 0
        return int(await self.db.scalar(sql, params) or 0)

    async def create_scheduled(
        self,
        *,
        guild_id: int,
        channel_id: int,
        author_id: int,
        title: str,
        body: str,
        ping_role_id: int | str | None,
        send_at: datetime,
        image_url: str | None = None,
        author_icon_url: str | None = None,
        footer_text: str | None = None,
        fields: list[dict[str, Any]] | None = None,
        color: int | str | None = None,
        source: str = "bot",
        conn: sqlite3.Connection | None = None,
    ) -> int:
        pending = await self.pending_count(guild_id, conn=conn)
        if pending >= self.config.announce_max_pending:
            raise AnnouncementLimitError(
                f"This server already has {pending} scheduled announcements "
                f"(limit {self.config.announce_max_pending}). Cancel one first."
            )
        payload = json.dumps(
            {
                "title": truncate(title, 256),
                "body": truncate(body, 4000),
                "ping_role_id": ping_role_id,
                "image_url": image_url,
                "author_icon_url": author_icon_url,
                "footer_text": truncate(footer_text.strip(), 2048) if footer_text and footer_text.strip() else None,
                "fields": fields or [],
                "color": color,
            },
            separators=(",", ":"),
        )
        sql = (
            "INSERT INTO announcements "
            "(guild_id, channel_id, author_id, payload, send_at, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'pending', ?)"
        )
        params = (guild_id, channel_id, author_id, payload, iso(send_at), iso())
        cursor = (
            await self.db.execute_on(conn, sql, params)
            if conn is not None
            else await self.db.execute(sql, params)
        )
        announcement_id = int(cursor.lastrowid or 0)
        await self.audit.record(
            guild_id=guild_id,
            actor_id=author_id,
            action="announce.create",
            target=str(announcement_id),
            detail={
                "channel_id": channel_id,
                "title": truncate(title, 256),
                "send_at": iso(send_at),
                "source": source,
            },
            conn=conn,
        )
        return announcement_id

    create = create_scheduled

    async def get(
        self, guild_id: int, announcement_id: int, *, conn: sqlite3.Connection | None = None
    ) -> dict[str, Any] | None:
        sql = (
            "SELECT id, guild_id, channel_id, author_id, payload, send_at, status, created_at "
            "FROM announcements WHERE id = ? AND guild_id = ?"
        )
        params = (announcement_id, guild_id)
        if conn is not None:
            return await self.db.fetchone_on(conn, sql, params)
        return await self.db.fetchone(sql, params)

    async def list_pending(
        self, guild_id: int, limit: int = 25, offset: int = 0
    ) -> list[dict[str, Any]]:
        return await self.db.fetchall(
            "SELECT id, channel_id, author_id, payload, send_at, status, created_at "
            "FROM announcements WHERE guild_id = ? AND status = 'pending' "
            "ORDER BY send_at ASC LIMIT ? OFFSET ?",
            (guild_id, max(1, min(limit, 50)), max(0, offset)),
        )

    async def list_recent(
        self, guild_id: int, limit: int = 15
    ) -> list[dict[str, Any]]:
        return await self.db.fetchall(
            "SELECT id, channel_id, author_id, payload, send_at, status, sent_at, error, created_at "
            "FROM announcements WHERE guild_id = ? AND status != 'pending' "
            "ORDER BY created_at DESC LIMIT ?",
            (guild_id, max(1, min(limit, 50))),
        )

    async def update(
        self,
        guild_id: int,
        announcement_id: int,
        *,
        title: str | None = None,
        body: str | None = None,
        channel_id: int | None = None,
        send_at: datetime | None = None,
        ping_role_id: int | str | None = None,
        image_url: str | None = None,
        author_icon_url: str | None = None,
        footer_text: str | None = None,
        fields: list[dict[str, Any]] | None = None,
        color: int | str | None = None,
        actor_id: int | None = None,
        source: str = "web",
        conn: sqlite3.Connection | None = None,
    ) -> bool:
        row = await self.get(guild_id, announcement_id, conn=conn)
        if not row or row["status"] != "pending":
            return False
        payload = json.loads(row["payload"] or "{}")
        if title is not None:
            payload["title"] = truncate(title, 256)
        if body is not None:
            payload["body"] = truncate(body, 4000)
        if ping_role_id is not None:
            payload["ping_role_id"] = ping_role_id if str(ping_role_id).strip() not in ("0", "") else None
        if image_url is not None:
            payload["image_url"] = image_url.strip() if image_url.strip() else None
        if author_icon_url is not None:
            payload["author_icon_url"] = author_icon_url.strip() if author_icon_url.strip() else None
        if footer_text is not None:
            payload["footer_text"] = truncate(footer_text.strip(), 2048) if footer_text.strip() else None
        if fields is not None:
            payload["fields"] = fields
        if color is not None:
            payload["color"] = color

        new_send_at = iso(send_at) if send_at is not None else row["send_at"]
        new_channel_id = channel_id if channel_id is not None else row["channel_id"]
        payload_str = json.dumps(payload, separators=(",", ":"))
        sql = "UPDATE announcements SET payload = ?, send_at = ?, channel_id = ? WHERE id = ? AND guild_id = ?"
        params = (payload_str, new_send_at, new_channel_id, announcement_id, guild_id)
        if conn is not None:
            await self.db.execute_on(conn, sql, params)
        else:
            await self.db.execute(sql, params)
        await self.audit.record(
            guild_id=guild_id,
            actor_id=actor_id,
            action="announce.update",
            target=str(announcement_id),
            detail={
                "title": payload.get("title"),
                "send_at": new_send_at,
                "channel_id": new_channel_id,
                "source": source,
            },
            conn=conn,
        )
        return True

    async def cancel(
        self,
        guild_id: int,
        announcement_id: int,
        actor_id: int,
        *,
        conn: sqlite3.Connection | None = None,
    ) -> bool:
        sql_fetch = "SELECT id, status FROM announcements WHERE id = ? AND guild_id = ?"
        params_fetch = (announcement_id, guild_id)
        row = (
            await self.db.fetchone_on(conn, sql_fetch, params_fetch)
            if conn is not None
            else await self.db.fetchone(sql_fetch, params_fetch)
        )
        if not row or row["status"] != "pending":
            return False
        sql_update = "UPDATE announcements SET status = 'cancelled' WHERE id = ? AND guild_id = ?"
        params_update = (announcement_id, guild_id)
        if conn is not None:
            await self.db.execute_on(conn, sql_update, params_update)
        else:
            await self.db.execute(sql_update, params_update)
        await self.audit.record(
            guild_id=guild_id,
            actor_id=actor_id,
            action="announce.cancel",
            target=str(announcement_id),
            detail={"source": "bot"},
            conn=conn,
        )
        return True

    async def _mark(self, announcement_id: int, status: str, error: str | None = None) -> None:
        await self.db.execute(
            "UPDATE announcements SET status = ?, sent_at = ?, error = ? WHERE id = ?",
            (status, iso() if status == "sent" else None, error, announcement_id),
        )

    async def dispatch_one(self, client: discord.Client, announcement_id: int) -> bool:
        row = await self.db.fetchone(
            "SELECT id, guild_id, channel_id, author_id, payload, send_at, status "
            "FROM announcements WHERE id = ?",
            (announcement_id,),
        )
        if not row or row["status"] != "pending":
            return False
        return await self._send_single_announcement(client, row)

    async def _send_single_announcement(
        self, client: discord.Client, row: dict[str, Any]
    ) -> bool:
        guild_id = int(row["guild_id"])
        payload = json.loads(row["payload"])
        channel = client.get_channel(int(row["channel_id"]))
        if channel is None:
            try:
                channel = await client.fetch_channel(int(row["channel_id"]))
            except Exception:
                channel = None
        image_url = payload.get("image_url")
        author_icon_url = payload.get("author_icon_url")
        footer_text = payload.get("footer_text")
        fields = payload.get("fields")
        color = payload.get("color")
        file = None
        if not image_url:
            default_bytes, ext = get_default_announcement_image()
            if default_bytes:
                filename = f"announcement.{ext}"
                file = discord.File(io.BytesIO(default_bytes), filename=filename)
                image_url = f"attachment://{filename}"

        embed = announcement_embed(
            payload.get("title", "Announcement"),
            payload.get("body", ""),
            author_icon_url=author_icon_url,
            image_url=image_url,
            footer_text=footer_text,
            fields=fields,
            color=color,
        )
        mentions = discord.AllowedMentions.none()
        content = None
        ping_role_id = payload.get("ping_role_id")
        if ping_role_id:
            role_val = str(ping_role_id).strip()
            if role_val in ("everyone", "@everyone"):
                content = "@everyone"
                mentions = discord.AllowedMentions(everyone=True)
            elif role_val in ("here", "@here"):
                content = "@here"
                mentions = discord.AllowedMentions(everyone=True)
            elif role_val.isdigit():
                content = f"<@&{role_val}>"
                mentions = discord.AllowedMentions(roles=True)

        last_error: str | None = None
        for attempt in range(3):
            try:
                if channel is None:
                    raise RuntimeError("channel no longer exists")
                if file is not None:
                    file.fp.seek(0)
                send_kwargs: dict[str, Any] = {
                    "content": content,
                    "embed": embed,
                    "allowed_mentions": mentions,
                }
                if file is not None:
                    send_kwargs["file"] = file
                await channel.send(**send_kwargs)
                last_error = None
                break
            except Exception as exc:  # noqa: BLE001 - reported to the author
                last_error = f"{type(exc).__name__}: {exc}"[:500]
                log.warning(
                    "announcement %s attempt %s failed: %s", row["id"], attempt + 1, last_error
                )
                if attempt < 2:
                    await asyncio.sleep(1.5)

        if last_error is None:
            await self._mark(int(row["id"]), "sent")
            await self.audit.record(
                guild_id=guild_id,
                actor_id=None,
                action="announce.sent",
                target=str(row["id"]),
                detail={"source": "system", "late": row["send_at"] < iso()},
            )
            return True
        else:
            await self._mark(int(row["id"]), "failed", last_error)
            await self._notify_failure(client, guild_id, int(row["author_id"]), row, last_error)
            return False

    async def dispatch(
        self,
        client: discord.Client,
        is_enabled: Callable[[int, str], Awaitable[bool]],
    ) -> DispatchResult:
        due = await self.db.fetchall(
            "SELECT a.id, a.guild_id, a.channel_id, a.author_id, a.payload, a.send_at "
            "FROM announcements a WHERE a.status = 'pending' AND a.send_at <= ? "
            "ORDER BY a.send_at ASC LIMIT ?",
            (iso(), self.config.announce_max_per_poll * 5),
        )
        sent = failed = skipped = 0
        handled = 0
        for row in due:
            if handled >= self.config.announce_max_per_poll:
                break
            guild_id = int(row["guild_id"])
            if not await is_enabled(guild_id, "dev"):
                skipped += 1
                continue
            handled += 1
            if await self._send_single_announcement(client, row):
                sent += 1
            else:
                failed += 1
        return DispatchResult(sent=sent, failed=failed, skipped=skipped)

    async def _notify_failure(
        self,
        client: discord.Client,
        guild_id: int,
        author_id: int,
        row: dict[str, Any],
        error: str,
    ) -> None:
        try:
            user = client.get_user(author_id) or await client.fetch_user(author_id)
            await user.send(
                f"Your scheduled announcement `#{row['id']}` in guild `{guild_id}` failed "
                f"after 3 attempts.\n`{truncate(error, 300)}`\n\n"
                "Check the channel still exists and that appardas can send embeds there."
            )
        except Exception:
            log.info("could not DM announcement author %s about the failure", author_id)
