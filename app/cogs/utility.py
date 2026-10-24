"""`utility` cog — help, about, invite, dashboard link.

Core: this is the door into everything else. `/help` returns a link, not a
wall of text, and offline cogs are struck through so a broken command is
explained where the user looks first.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from ..checks import ensure_guild, has_manage_guild
from ..cogs.registry import COGS
from ..util import duration_text
from ..version import VERSION
from ..views.embeds import BRAND_COLOR, MUTED_COLOR

log = logging.getLogger(__name__)


class HelpView(discord.ui.View):
    def __init__(self, url: str) -> None:
        super().__init__(timeout=180)
        self.add_item(
            discord.ui.Button(
                label="Open command docs",
                style=discord.ButtonStyle.link,
                url=url,
            )
        )


class UtilityCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.hybrid_command(
        name="help", description="A link to every command, deep-linked to one cog if given."
    )
    @app_commands.describe(cog="Jump straight to this cog's page.")
    @app_commands.choices(cog=[app_commands.Choice(name=key, value=key) for key in COGS])
    async def help(
        self, ctx: commands.Context, cog: str | None = None
    ) -> None:
        if cog is not None and cog not in COGS:
            await ctx.send("No such cog. Choose " + ", ".join(COGS) + ".", ephemeral=True)
            return
        base = self.bot.cfg.public_base_url
        guild = await ensure_guild(ctx)
        states = await self.bot.settings_service.cog_states(guild.id)
        lines = []
        for key, meta in COGS.items():
            state = states[key]
            if state.state == "offline":
                lines.append(f"~~{key}~~ (offline)")
            elif state.state == "faulted":
                lines.append(f"~~{key}~~ (temporarily unavailable)")
            else:
                lines.append(key)
        description = (
            "Every command, with examples and permissions.\n\n"
            "Jump straight to: " + " · ".join(lines)
        )
        embed = discord.Embed(title="appardas help", description=description, color=BRAND_COLOR)
        prefix = await self.bot.settings_service.command_prefix(guild.id)
        shown = discord.utils.escape_markdown(prefix) if prefix else "mention-only"
        embed.add_field(
            name="Text commands",
            value=f"Prefix: **{shown}**. Bot mentions also work. Change it with `/settings prefix`. "
            "Slash commands always use `/`; text command replies are visible in the channel.",
            inline=False,
        )
        embed.set_footer(text=f"version {VERSION}")
        url = f"{base}/commands/{cog}" if cog else f"{base}/commands"
        await ctx.send(embed=embed, view=HelpView(url), ephemeral=True)

    @commands.hybrid_command(name="about", description="Version, uptime, links and host budget.")
    async def about(self, ctx: commands.Context) -> None:
        await ensure_guild(ctx)
        snapshot = await self.bot.status_service.snapshot()
        embed = discord.Embed(
            title="appardas",
            description=(
                "A small, dependable Discord bot built as five cogs. "
                "Each one can be paused without losing its work."
            ),
            color=BRAND_COLOR,
        )
        embed.add_field(name="Version", value=VERSION, inline=True)
        embed.add_field(
            name="Uptime", value=duration_text(snapshot.get("uptime_s", 0)), inline=True
        )
        embed.add_field(
            name="Guilds", value=str(snapshot.get("discord", {}).get("guilds", 0)), inline=True
        )
        embed.add_field(
            name="Runs on",
            value="512 MB / 35% vCPU free tier · SQLite · one process",
            inline=False,
        )
        links = []
        links.append(f"[Commands]({self.bot.cfg.public_base_url}/commands)")
        links.append(f"[Dashboard]({self.bot.cfg.public_base_url}/dashboard)")
        if links:
            embed.add_field(name="Links", value=" · ".join(links), inline=False)
        embed.set_footer(text="Renewed manually every four days; /status shows the uptime clock.")
        await ctx.send(embed=embed, ephemeral=True)

    @commands.hybrid_command(name="invite", description="Install link with the minimal permissions.")
    async def invite(self, ctx: commands.Context) -> None:
        await ensure_guild(ctx)
        await ctx.send(
            "Appardas is an exclusive operational bot for designated servers. Direct public installation is disabled.",
            ephemeral=True,
        )

    def _invite_url(self) -> str | None:
        return None

    @app_commands.command(
        name="dashboard", description="Open the persistent dashboard. Access is granted by the owner."
    )
    @app_commands.default_permissions(manage_guild=True)
    async def dashboard(self, interaction: discord.Interaction) -> None:
        await has_manage_guild(interaction)
        await ensure_guild(interaction)
        url = f"{self.bot.cfg.public_base_url}/dashboard"
        await interaction.response.send_message(
            f"Dashboard:\n{url}\nSign in with operator credentials. Dashboard access is restricted to authorized operators.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(UtilityCog(bot))
