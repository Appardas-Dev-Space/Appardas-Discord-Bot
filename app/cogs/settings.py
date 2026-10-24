"""`settings` cog — the maintenance switch, guild config and audit view.

Core: cannot be disabled. If you could turn this off, there would be no path
back to turning anything else back on.
"""

from __future__ import annotations

import json
import logging
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord
from discord import app_commands
from discord.ext import commands

from ..checks import ensure_guild, has_manage_guild
from ..cogs.registry import COGS, LOCKED, TOGGLEABLE
from ..services.settings import CONFIG_KEYS, LockedCogError, UnknownCogError
from ..util import truncate
from ..views.embeds import BRAND_COLOR

log = logging.getLogger(__name__)

STATE_CHOICES = [
    app_commands.Choice(name="on", value="on"),
    app_commands.Choice(name="off", value="off"),
]
CONFIG_CHOICES = [app_commands.Choice(name=key, value=key) for key in CONFIG_KEYS]


class SettingsCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @property
    def settings(self):
        return self.bot.settings_service  # type: ignore[attr-defined]

    async def _board(self, guild: discord.Guild) -> discord.Embed:
        states = await self.settings.cog_states(guild.id)
        lines: list[str] = []
        for key, cog in COGS.items():
            state = states[key]
            word = {"online": "● online", "offline": "○ offline", "faulted": "! faulted"}[state.state]
            detail = "(core)" if cog.locked else ""
            if key == "dev" and getattr(self.bot, "announcements", None):
                pending = await self.bot.announcements.pending_count(guild.id)
                if pending:
                    detail = f"{pending} scheduled" if not detail else detail
            if key == "github" and getattr(self.bot, "github", None):
                watches = await self.bot.github.watch_count(guild.id)
                if watches:
                    detail = f"{watches} watch{'es' if watches != 1 else ''}" if not detail else detail
            if state.reason and state.state == "offline":
                detail = f"reason: {truncate(state.reason, 80)}"
            lines.append(f"{key:<9} {word:<10} {detail}".rstrip())
        embed = discord.Embed(
            title="Cogs",
            description="```\n" + "\n".join(lines) + "\n```",
            color=BRAND_COLOR,
        )
        embed.add_field(
            name="Toggle",
            value="`/settings cog <name> <on|off> [reason]`",
            inline=False,
        )
        embed.add_field(
            name="Locked",
            value=", ".join(f"`{key}`" for key in sorted(LOCKED)) + " cannot be disabled.",
            inline=False,
        )
        embed.set_footer(text="Off pauses commands, loops and scheduled work. It deletes nothing.")
        return embed

    @commands.hybrid_group(
        name="settings", description="Cog toggles, guild config and audit.", invoke_without_command=True
    )
    async def settings_group(self, ctx: commands.Context) -> None:
        await has_manage_guild(ctx)
        guild = await ensure_guild(ctx)
        await ctx.send(embed=await self._board(guild), ephemeral=True)

    @settings_group.command(name="cogs", description="Every cog, its state and how to toggle it.")
    @app_commands.default_permissions(manage_guild=True)
    async def cogs(self, ctx: commands.Context) -> None:
        await has_manage_guild(ctx)
        guild = await ensure_guild(ctx)
        await ctx.send(embed=await self._board(guild), ephemeral=True)

    @settings_group.command(
        name="cog",
        description="Turn a cog on or off for maintenance. Off pauses work; it never deletes it.",
    )
    @app_commands.describe(
        name="The cog to change.",
        state="on or off.",
        reason="Why. This string is shown to users and written to the audit log.",
    )
    @app_commands.choices(
        name=[app_commands.Choice(name=key, value=key) for key in COGS],
        state=STATE_CHOICES,
    )
    @app_commands.default_permissions(manage_guild=True)
    async def cog(
        self,
        ctx: commands.Context,
        name: str,
        state: str,
        *,
        reason: str | None = None,
    ) -> None:
        await has_manage_guild(ctx)
        guild = await ensure_guild(ctx)
        if state not in ("on", "off"):
            await ctx.send("Choose `on` or `off`.", ephemeral=True)
            return
        enabled = state == "on"
        try:
            result = await self.settings.set_cog_state(
                guild.id,
                name,
                enabled,
                ctx.author.id,
                reason,
                source="bot",
            )
        except LockedCogError:
            await ctx.send(
                self.settings.describe_lock(name)
                + " Disabling it would leave no way back.",
                ephemeral=True,
            )
            return
        except UnknownCogError:
            await ctx.send("No such cog.", ephemeral=True)
            return

        if enabled:
            message = f"`{name}` is now **online**. Commands work immediately; held work resumes."
        else:
            pending = await self.bot.announcements.pending_count(guild.id) if name == "dev" else 0
            message = (
                f"`{name}` is now **offline**. Commands reply with the reason; "
                "background loops skip; scheduled work is held, not cancelled."
            )
            if pending:
                message += f"\n{pending} scheduled announcement(s) held."
        message += "\nRecorded in the audit log."
        await ctx.send(message, ephemeral=True)

    async def _prefix(self, ctx: commands.Context, value: str | None = None) -> None:
        await has_manage_guild(ctx)
        guild = await ensure_guild(ctx)
        if value is None or not value.strip():
            current = await self.settings.command_prefix(guild.id)
            shown = discord.utils.escape_markdown(current) if current else "not set (mention-only)"
            message = f"Text command prefix: **{shown}**. Bot mentions and slash commands still work."
            if current and not self.bot.intents.message_content:
                message += "\nCustom prefixes are inactive: enable Message Content Intent on the host and in the portal."
            await ctx.send(message, ephemeral=True)
            return
        trimmed = value.strip()
        prefix = None if trimmed.lower() in ("reset", "none", "clear") else trimmed
        if prefix is not None and not self.bot.intents.message_content:
            await ctx.send(
                "Custom text prefixes require `ENABLE_MESSAGE_CONTENT_INTENT=true` on the host "
                "and **Message Content Intent** in Discord Developer Portal → Bot. "
                "Restart once after enabling them, then set the prefix. Slash commands and bot mentions still work.",
                ephemeral=True,
            )
            return
        try:
            await self.settings.update_setting(guild.id, "prefix", prefix, ctx.author.id, source="bot")
        except ValueError as exc:
            await ctx.send(str(exc), ephemeral=True)
            return
        if prefix is None:
            message = "Text commands are now **mention-only**. Slash commands still use `/`."
        else:
            shown = discord.utils.escape_markdown(prefix)
            message = f"Text command prefix set to **{shown}**, effective immediately. Bot mentions still work; slash commands still use `/`."
        await ctx.send(message + "\nRecorded in the audit log.", ephemeral=True)

    @settings_group.command(name="prefix", description="Read or change this server's text command prefix.")
    @app_commands.describe(value="New prefix (1–16 characters), or reset for mention-only. Omit to read.")
    @app_commands.default_permissions(manage_guild=True)
    async def prefix(self, ctx: commands.Context, *, value: str | None = None) -> None:
        await self._prefix(ctx, value)

    @settings_group.command(
        name="config", description="Read or set channels, timezone or the text command prefix."
    )
    @app_commands.describe(
        key="Which setting.",
        value="New value. Omit to read the current value.",
    )
    @app_commands.choices(key=CONFIG_CHOICES)
    @app_commands.default_permissions(manage_guild=True)
    async def config(
        self,
        ctx: commands.Context,
        key: str,
        *,
        value: str | None = None,
    ) -> None:
        await has_manage_guild(ctx)
        guild = await ensure_guild(ctx)
        name = key
        if name not in CONFIG_KEYS:
            await ctx.send("Unknown setting. Choose " + ", ".join(CONFIG_KEYS) + ".", ephemeral=True)
            return
        if name == "prefix":
            await self._prefix(ctx, value)
            return
        if value is None or value.strip() == "":
            current = await self.settings.get_setting(guild.id, name)
            shown = f"<#{current}>" if name != "timezone" and current else (current or "not set")
            await ctx.send(
                f"`{name}` = {shown}", ephemeral=True
            )
            return

        raw = value.strip()
        parsed: object
        if name == "timezone":
            try:
                ZoneInfo(raw)
            except (ZoneInfoNotFoundError, ValueError):
                await ctx.send(
                    f"`{raw}` is not an IANA timezone. Try `Europe/Berlin` or `America/New_York`.",
                    ephemeral=True,
                )
                return
            parsed = raw
        else:
            channel = None
            if raw.startswith("<#") and raw.endswith(">"):
                raw = raw[2:-1]
            if raw.isdigit():
                channel = guild.get_channel(int(raw))
            if channel is None:
                channel = discord.utils.get(guild.text_channels, name=raw.lstrip("#"))
            if not isinstance(channel, discord.TextChannel):
                await ctx.send(
                    f"`{raw}` is not a text channel in this server. Mention it as `#channel`.",
                    ephemeral=True,
                )
                return
            permissions = channel.permissions_for(guild.me)
            if not permissions.send_messages or not permissions.embed_links:
                await ctx.send(
                    f"I need **Send Messages** and **Embed Links** in {channel.mention} first.",
                    ephemeral=True,
                )
                return
            parsed = channel.id

        await self.settings.update_setting(
            guild.id, name, parsed, ctx.author.id, source="bot"
        )
        shown = f"<#{parsed}>" if name != "timezone" else parsed
        await ctx.send(
            f"`{name}` set to {shown}.", ephemeral=True
        )

    @settings_group.command(name="audit", description="Who changed what, when, and why.")
    @app_commands.describe(limit="How many rows (1–25).")
    @app_commands.default_permissions(manage_guild=True)
    async def audit(
        self, ctx: commands.Context, limit: commands.Range[int, 1, 25] = 10
    ) -> None:
        await has_manage_guild(ctx)
        guild = await ensure_guild(ctx)
        rows = await self.bot.audit.tail(guild.id, limit)
        if not rows:
            await ctx.send(
                "Nothing has been recorded yet.", ephemeral=True
            )
            return
        lines = []
        for row in rows:
            stamp = str(row["created_at"]).replace("T", " ").replace("Z", "")
            detail = {}
            try:
                detail = json.loads(row["detail"] or "{}")
            except Exception:
                pass
            reason = detail.get("reason") or detail.get("after") or ""
            actor = f"<@{row['actor_id']}>" if row["actor_id"] else "system"
            lines.append(
                f"{stamp[:16]} · {actor} · {row['action']} · {row['target'] or '—'}"
                + (f" · {truncate(str(reason), 60)}" if reason else "")
            )
        embed = discord.Embed(
            title="Audit log",
            description="```\n" + truncate("\n".join(lines), 3900) + "\n```",
            color=BRAND_COLOR,
        )
        embed.set_footer(text=f"Showing {len(rows)} · retained {self.bot.cfg.audit_retention_days} days")
        await ctx.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SettingsCog(bot))
