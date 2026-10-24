"""`status` cog — is the bot alive, and is anything wrong?

Owns the public health commands and the owner-only `/appardas` operations.
Core: cannot be disabled, because this is how an operator finds out the bot
is unhealthy in the first place.
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import time
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from ..checks import ensure_guild, has_manage_guild, is_owner
from ..cogs.registry import COGS
from ..commands_export import write_manifest
from ..util import ago_text, duration_text, human_bytes, iso, truncate
from ..views.embeds import BRAND_COLOR, MUTED_COLOR, BAD_COLOR
from ..version import VERSION

log = logging.getLogger(__name__)


def _state_word(state: str) -> str:
    if state == "online":
        return "● online "
    if state == "offline":
        return "○ offline"
    return "! faulted"


class StatusCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ------------------------------------------------------------- helpers
    async def _cog_rows(self, guild_id: int) -> list[str]:
        settings = getattr(self.bot, "settings_service", None)
        if settings is None:
            return []
        states = await settings.cog_states(guild_id)
        pending = 0
        if getattr(self.bot, "announcements", None) is not None:
            pending = await self.bot.announcements.pending_count(guild_id)
        watches: list[dict[str, Any]] = []
        if getattr(self.bot, "github", None) is not None:
            watches = await self.bot.github.list_watches(guild_id)
        rows: list[str] = []
        for key, cog in COGS.items():
            state = states[key]
            detail = "(core)" if cog.locked else ""
            if key == "github":
                if watches:
                    latest = max(
                        (watch["last_polled"] for watch in watches if watch.get("last_polled")),
                        default=None,
                    )
                    detail = f"{len(watches)} watch{'es' if len(watches) != 1 else ''}"
                    if latest:
                        from ..util import parse_iso

                        detail += f" · last poll {ago_text(parse_iso(latest))}"
                else:
                    detail = "no watches"
            elif key == "dev":
                detail = f"{pending} pending" if pending else "no scheduled work"
            if state.state == "offline" and state.reason:
                detail = f"{detail} · reason: {truncate(state.reason, 60)}".strip(" ·")
            if state.state == "faulted":
                fault = (getattr(self.bot, "cog_faults", {}) or {}).get(key, "unknown error")
                detail = f"{detail} · {fault}".strip(" ·")
            rows.append(f"  {key:<9} {_state_word(state.state)}   {detail}".rstrip())
        return rows

    async def _status_embed(self, guild: discord.Guild) -> discord.Embed:
        status_service = getattr(self.bot, "status_service", None)
        snapshot = await status_service.snapshot() if status_service else {}
        discord_info = snapshot.get("discord", {})
        db_info = snapshot.get("db", {})
        uptime = duration_text(snapshot.get("uptime_s", 0))
        gateway = discord_info.get("gateway_latency_ms")
        rest = discord_info.get("rest_latency_ms")
        db_latency = db_info.get("latency_ms")
        rss = snapshot.get("mem_rss_mb", 0)
        cpu = snapshot.get("cpu_pct", 0)
        memory_pct = round((rss / 512) * 100, 1) if rss else 0

        def ms(value: Any) -> str:
            return f"{value:.0f} ms" if isinstance(value, (int, float)) else "—"

        lines = [
            f"appardas {VERSION} · up {uptime}",
            "",
            f"Latency      gateway {ms(gateway)} · REST {ms(rest)} · DB {ms(db_latency)}",
            f"Memory       {rss:.1f} MB / 512 MB  ({memory_pct:.0f}%)",
            f"CPU          {cpu:.1f}% of 35% allotted",
            f"Guilds       {discord_info.get('guilds', 0)} · {discord_info.get('members_cached', 0)} members cached",
            f"SQLite       {human_bytes(int(db_info.get('size_bytes') or 0))} · WAL",
            "",
            "Cogs",
        ]
        lines.extend(await self._cog_rows(guild.id))
        embed = discord.Embed(
            title="appardas status",
            description=f"```\n{truncate(chr(10).join(lines), 3900)}\n```",
            color=BRAND_COLOR if snapshot.get("status") == "ok" else BAD_COLOR,
        )
        embed.set_footer(text=f"guild latency {ms(discord_info.get('gateway_latency_ms'))}")
        return embed

    async def _errors_embed(self, limit: int) -> discord.Embed:
        rows = await self.bot.db.fetchall(
            "SELECT occurred_at, cog, command, error_type FROM cog_errors "
            "ORDER BY occurred_at DESC, id DESC LIMIT ?",
            (max(1, min(limit, 25)),),
        )
        if not rows:
            return discord.Embed(
                title="No unhandled errors",
                description="Nothing has been recorded since this database was created.",
                color=MUTED_COLOR,
            )
        lines = []
        for row in rows:
            stamp = str(row["occurred_at"]).replace("T", " ").replace("Z", "")
            lines.append(
                f"{stamp[:16]} · {row['cog']} · {row['command'] or '—'} · {row['error_type']}"
            )
        return discord.Embed(
            title=f"Last {len(rows)} unhandled error(s)",
            description="```\n" + truncate("\n".join(lines), 3900) + "\n```",
            color=BAD_COLOR,
        ).set_footer(text="No message content, no user IDs.")

    # ---------------------------------------------------------------- public
    @commands.hybrid_command(
        name="status",
        description="Uptime, latency, memory, CPU, guilds and the five-cog table.",
    )
    @app_commands.describe(
        errors="Show the last unhandled command errors instead of the status board.",
        limit="How many error rows to show (1–25).",
    )
    async def status(
        self,
        ctx: commands.Context,
        errors: bool = False,
        limit: commands.Range[int, 1, 25] = 10,
    ) -> None:
        guild = await ensure_guild(ctx)
        if errors:
            await has_manage_guild(ctx)
            await ctx.send(
                embed=await self._errors_embed(limit), ephemeral=True
            )
            return
        await ctx.send(embed=await self._status_embed(guild))

    @commands.hybrid_command(
        name="errors",
        description="The last unhandled command errors: time, cog, command, exception type.",
    )
    @app_commands.describe(limit="How many rows to show (1–25).")
    @app_commands.default_permissions(manage_guild=True)
    async def errors(
        self, ctx: commands.Context, limit: commands.Range[int, 1, 25] = 10
    ) -> None:
        await has_manage_guild(ctx)
        await ensure_guild(ctx)
        await ctx.send(
            embed=await self._errors_embed(limit), ephemeral=True
        )

    @commands.hybrid_command(
        name="ping", description="Gateway, REST and database latency as three numbers."
    )
    async def ping(self, ctx: commands.Context) -> None:
        await ensure_guild(ctx)
        gateway = round(self.bot.latency * 1000, 1) if self.bot.latency else None
        rest: float | None = None
        started = time.perf_counter()
        try:
            await self.bot.application_info()
            rest = round((time.perf_counter() - started) * 1000, 1)
            if getattr(self.bot, "status_service", None):
                self.bot.status_service.set_rest_latency(rest)
        except Exception:
            log.warning("REST ping failed", exc_info=True)

        started = time.perf_counter()
        await self.bot.db.fetchone("SELECT 1")
        db_ms = round((time.perf_counter() - started) * 1000, 1)

        def ms(value: float | None) -> str:
            return f"{value:.0f} ms" if value is not None else "—"

        embed = discord.Embed(
            title="pong",
            description=(
                f"gateway **{ms(gateway)}**\nREST **{ms(rest)}**\ndb **{ms(db_ms)}**"
            ),
            color=BRAND_COLOR,
        )
        await ctx.send(embed=embed)

    async def _guild(self, interaction: discord.Interaction) -> discord.Guild:
        if interaction.guild is None:
            raise app_commands.CheckFailure("This command only works inside a server.")
        if getattr(self.bot, "settings_service", None):
            await self.bot.settings_service.ensure_guild(
                interaction.guild.id, interaction.guild.name
            )
        return interaction.guild

    # ---------------------------------------------------------------- owner
    appardas = app_commands.Group(name="appardas", description="Owner operations.")

    @appardas.command(name="ping", description="Alias of /ping.")
    @app_commands.check(is_owner)
    async def appardas_ping(self, interaction: discord.Interaction) -> None:
        ctx = await self.bot.get_context(interaction)
        await self.ping.callback(self, ctx)

    @appardas.command(name="status", description="Alias of /status.")
    @app_commands.check(is_owner)
    async def appardas_status(self, interaction: discord.Interaction) -> None:
        guild = await self._guild(interaction)
        await interaction.response.send_message(embed=await self._status_embed(guild))

    @appardas.command(name="errors", description="Alias of /errors.")
    @app_commands.describe(limit="How many rows to show (1–25).")
    @app_commands.check(is_owner)
    async def appardas_errors(
        self, interaction: discord.Interaction, limit: app_commands.Range[int, 1, 25] = 10
    ) -> None:
        await interaction.response.send_message(
            embed=await self._errors_embed(limit), ephemeral=True
        )

    @appardas.command(name="reload", description="Reload one cog after fixing a fault.")
    @app_commands.describe(cog="The cog to reload.")
    @app_commands.choices(
        cog=[app_commands.Choice(name=key, value=key) for key in COGS]
    )
    @app_commands.check(is_owner)
    async def appardas_reload(self, interaction: discord.Interaction, cog: str) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            await self.bot.reload_cog(cog)  # type: ignore[attr-defined]
        except Exception as exc:
            await interaction.followup.send(
                f"Reload of `{cog}` failed: `{type(exc).__name__}: {truncate(str(exc), 300)}`. "
                "The previous fault stays recorded; the other cogs keep running.",
                ephemeral=True,
            )
            return
        await interaction.followup.send(f"`{cog}` reloaded. Fault cleared.", ephemeral=True)

    @appardas.command(name="sync", description="Sync the Discord command tree.")
    @app_commands.check(is_owner)
    async def appardas_sync(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            synced, scope = await self.bot.sync_tree()  # type: ignore[attr-defined]
            await interaction.followup.send(
                f"Synced {synced} commands ({scope}).", ephemeral=True
            )
            if getattr(self.bot, "status_service", None):
                self.bot.status_service.mark_scheduler("sync")
        except Exception as exc:
            await interaction.followup.send(
                f"Sync failed: `{type(exc).__name__}: {truncate(str(exc), 300)}`", ephemeral=True
            )

    @appardas.command(
        name="backup", description="Export settings, watches, pending announcements and opt-outs."
    )
    @app_commands.check(is_owner)
    async def appardas_backup(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        export = await self._build_export()
        payload = json.dumps(export, indent=2, default=str).encode("utf-8")
        file = discord.File(io.BytesIO(payload), filename="appardas-export.json")
        await interaction.followup.send(
            "Export attached. It contains no tokens and no nudge message bodies.",
            file=file,
            ephemeral=True,
        )

    async def _build_export(self) -> dict[str, Any]:
        guilds = await self.bot.db.fetchall("SELECT id, name, joined_at, left_at FROM guilds")
        settings = await self.bot.db.fetchall(
            "SELECT guild_id, announce_channel, github_channel, mod_log_channel, timezone, settings, updated_at "
            "FROM guild_settings"
        )
        cog_state = await self.bot.db.fetchall(
            "SELECT guild_id, cog, enabled, reason, updated_by, updated_at FROM cog_state"
        )
        watches = await self.bot.db.fetchall(
            "SELECT guild_id, target, target_type, channel_id, events, last_polled, "
            "last_delivered, state, created_by FROM github_watches"
        )
        pending = await self.bot.db.fetchall(
            "SELECT id, guild_id, channel_id, payload, send_at, status, created_at "
            "FROM announcements WHERE status = 'pending'"
        )
        optouts = await self.bot.db.fetchall("SELECT user_id, created_at FROM nudge_optouts")
        return {
            "version": VERSION,
            "exported_at": iso(),
            "guilds": guilds,
            "guild_settings": settings,
            "cog_state": cog_state,
            "github_watches": watches,
            "pending_announcements": pending,
            "nudge_optouts": optouts,
        }

    @appardas.command(name="renew", description="Stamp the deployment renewal clock.")
    @app_commands.check(is_owner)
    async def appardas_renew(self, interaction: discord.Interaction) -> None:
        now = iso()
        await self.bot.db.set_meta("last_renewed_at", now)
        await interaction.response.send_message(
            f"Renewal clock stamped at `{now}`. The dashboard now shows the next 4-day cycle.",
            ephemeral=True,
        )

    @appardas.command(name="shutdown", description="Close loops and the process cleanly.")
    @app_commands.check(is_owner)
    async def appardas_shutdown(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            "Shutting down cleanly. The host may restart the process.", ephemeral=True
        )
        log.warning("shutdown requested by %s", interaction.user.id)
        self.bot.shutdown_requested = True  # type: ignore[attr-defined]
        await self.bot.close()

    @appardas.command(
        name="dashboard", description="Open the persistent, account-gated dashboard."
    )
    @app_commands.check(is_owner)
    async def appardas_dashboard(self, interaction: discord.Interaction) -> None:
        url = f"{self.bot.cfg.public_base_url}/dashboard"
        await interaction.response.send_message(
            f"Dashboard:\n{url}\nSign in with Discord. Accounts and server access are managed here by the owner.",
            ephemeral=True,
        )

    @appardas.command(
        name="dump-commands", description="Regenerate app/commands.json from the live tree."
    )
    @app_commands.check(is_owner)
    async def appardas_dump_commands(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            path = await asyncio.to_thread(write_manifest, self.bot.tree)
        except Exception as exc:
            await interaction.followup.send(
                f"Refused: `{type(exc).__name__}: {truncate(str(exc), 500)}`", ephemeral=True
            )
            return
        await interaction.followup.send(f"Wrote `{path}`.", ephemeral=True)

    @appardas.command(
        name="presence", description="Update the bot status bio and activity dynamically."
    )
    @app_commands.describe(
        text="Status text, or 'reset' to revert to default config.",
        type="Activity prefix: custom (text only), watching, playing, listening, competing.",
        status="Online status: online, idle, dnd, invisible.",
    )
    @app_commands.choices(
        type=[
            app_commands.Choice(name="custom (text only, bio)", value="custom"),
            app_commands.Choice(name="watching", value="watching"),
            app_commands.Choice(name="playing", value="playing"),
            app_commands.Choice(name="listening", value="listening"),
            app_commands.Choice(name="competing", value="competing"),
        ],
        status=[
            app_commands.Choice(name="online (green)", value="online"),
            app_commands.Choice(name="idle (orange/moon)", value="idle"),
            app_commands.Choice(name="dnd (red/do not disturb)", value="dnd"),
            app_commands.Choice(name="invisible (offline)", value="invisible"),
        ],
    )
    @app_commands.check(is_owner)
    async def appardas_presence(
        self,
        interaction: discord.Interaction,
        text: str,
        type: str | None = None,
        status: str | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            info = await self.bot.update_presence(text=text, type=type, status=status)  # type: ignore[attr-defined]
            msg = (
                f"Bot presence updated:\n"
                f"• **Text**: `{info['text']}`\n"
                f"• **Type**: `{info['type']}`\n"
                f"• **Status**: `{info['status']}`"
            )
            if not info.get("is_custom"):
                msg += "\n*(reverted to default config)*"
            await interaction.followup.send(msg, ephemeral=True)
        except Exception as exc:
            log.exception("failed to update presence")
            await interaction.followup.send(f"Failed to update presence: `{exc}`", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(StatusCog(bot))
