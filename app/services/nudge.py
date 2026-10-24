"""Nudges: one private DM, sent on the author's behalf, with five guardrails.

The DM abuse path is the real threat model, so every attempt is counted,
capped and audited — including the ones that never leave the server.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import timedelta

import discord

from ..config import Settings
from ..db import Database
from ..util import iso, utcnow
from ..views.embeds import nudge_embed
from .audit import AuditService

log = logging.getLogger(__name__)

MAX_MESSAGE = 500


class NudgeError(ValueError):
    """A refusal with a user-facing explanation."""


@dataclass(frozen=True, slots=True)
class NudgeResult:
    delivered: bool
    detail: str
    target_id: int


class NudgeService:
    def __init__(self, db: Database, config: Settings, audit: AuditService) -> None:
        self.db = db
        self.config = config
        self.audit = audit
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------- opt-outs
    async def is_opted_out(self, user_id: int) -> bool:
        row = await self.db.fetchone(
            "SELECT user_id FROM nudge_optouts WHERE user_id = ?", (user_id,)
        )
        return row is not None

    async def opt_out(self, user_id: int) -> None:
        await self.db.execute(
            "INSERT INTO nudge_optouts (user_id, created_at) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO NOTHING",
            (user_id, iso()),
        )

    async def opt_in(self, user_id: int) -> None:
        await self.db.execute("DELETE FROM nudge_optouts WHERE user_id = ?", (user_id,))

    # ---------------------------------------------------------------- send
    async def send(
        self,
        *,
        guild: discord.Guild,
        author: discord.abc.User,
        target: discord.Member,
        message: str,
    ) -> NudgeResult:
        if not self.config.nudge_enabled:
            raise NudgeError("Nudges are disabled globally by the bot operator.")
        message = (message or "").strip()
        if not message:
            raise NudgeError("The nudge message is empty.")
        if len(message) > MAX_MESSAGE:
            raise NudgeError(f"Keep the message under {MAX_MESSAGE} characters.")

        if target.id == author.id:
            raise NudgeError("You cannot nudge yourself.")
        if target.bot:
            raise NudgeError("You cannot nudge a bot.")
        if target.guild.id != guild.id:
            raise NudgeError("That member is not in this server.")
        if await self.is_opted_out(target.id):
            raise NudgeError(
                f"{target.display_name} has opted out of nudges. Respect that — no message was sent."
            )

        async with self._lock:
            now = utcnow()
            pair_since = iso(now - timedelta(seconds=self.config.nudge_pair_cooldown_s))
            pair = await self.db.fetchone(
                "SELECT created_at FROM nudges WHERE guild_id = ? AND author_id = ? "
                "AND target_id = ? AND created_at >= ? ORDER BY created_at DESC LIMIT 1",
                (guild.id, author.id, target.id, pair_since),
            )
            if pair:
                raise NudgeError(
                    f"You already nudged {target.display_name} in the last "
                    f"{self.config.nudge_pair_cooldown_s}s. Give it a minute."
                )

            hour_since = iso(now - timedelta(hours=1))
            hourly = int(
                await self.db.scalar(
                    "SELECT COUNT(*) FROM nudges WHERE guild_id = ? AND author_id = ? "
                    "AND created_at >= ?",
                    (guild.id, author.id, hour_since),
                )
                or 0
            )
            if hourly >= self.config.nudge_max_per_hour:
                raise NudgeError(
                    f"You have reached {self.config.nudge_max_per_hour} nudges this hour. "
                    "Try again later."
                )

            day_since = iso(now - timedelta(days=1))
            daily = int(
                await self.db.scalar(
                    "SELECT COUNT(*) FROM nudges WHERE guild_id = ? AND target_id = ? "
                    "AND created_at >= ?",
                    (guild.id, target.id, day_since),
                )
                or 0
            )
            if daily >= self.config.nudge_max_per_target_day:
                raise NudgeError(
                    f"{target.display_name} has received {daily} nudges in the last 24 hours "
                    f"(limit {self.config.nudge_max_per_target_day}). Try tomorrow."
                )

            cursor = await self.db.execute(
                "INSERT INTO nudges (guild_id, author_id, target_id, message, delivered, created_at) "
                "VALUES (?, ?, ?, ?, 0, ?)",
                (guild.id, author.id, target.id, message, iso()),
            )
            nudge_id = int(cursor.lastrowid or 0)

        delivered = False
        detail = "Delivered."
        try:
            await target.send(
                embed=nudge_embed(author.display_name, guild.name, message),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            delivered = True
        except discord.Forbidden:
            detail = "Could not DM them — their direct messages are closed."
        except discord.HTTPException as exc:
            detail = "Discord rejected the DM. Try again in a moment."
            log.warning("nudge %s HTTP failure: %s", nudge_id, exc)
        except Exception:
            detail = "Could not deliver the DM. Try again in a moment."
            log.exception("nudge %s failed", nudge_id)

        if delivered:
            await self.db.execute("UPDATE nudges SET delivered = 1 WHERE id = ?", (nudge_id,))
        await self.audit.record(
            guild_id=guild.id,
            actor_id=author.id,
            action="nudge.send",
            target=str(target.id),
            detail={"delivered": delivered, "nudge_id": nudge_id, "source": "bot"},
        )
        return NudgeResult(delivered=delivered, detail=detail, target_id=target.id)

    async def stats_since(self, guild_id: int, days: int = 7) -> dict[str, int]:
        since = iso(utcnow() - timedelta(days=days))
        row = await self.db.fetchone(
            "SELECT COUNT(*) AS sent, COALESCE(SUM(delivered), 0) AS delivered "
            "FROM nudges WHERE guild_id = ? AND created_at >= ?",
            (guild_id, since),
        )
        return {"sent": int((row or {}).get("sent") or 0), "delivered": int((row or {}).get("delivered") or 0)}
