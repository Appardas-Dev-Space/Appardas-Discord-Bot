"""`github` cog — org and repo watches, grouped posts, honest health words."""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from ..checks import ensure_guild, has_manage_guild
from ..services.github import (
    DEFAULT_EVENTS,
    EVENT_TYPES,
    GitHubError,
    WatchLimitError,
    normalise_events,
    normalise_target,
)
from ..util import truncate
from ..views.embeds import BRAND_COLOR

log = logging.getLogger(__name__)


class GithubCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @property
    def github(self):
        return self.bot.github  # type: ignore[attr-defined]

    @property
    def settings(self):
        return self.bot.settings_service  # type: ignore[attr-defined]

    async def _target_channel(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel | None,
    ) -> discord.TextChannel | None:
        guild = ctx.guild
        if guild is None:
            return None
        if channel is None:
            configured = await self.settings.get_setting(guild.id, "github_channel")
            if configured:
                candidate = guild.get_channel(int(configured))
                if isinstance(candidate, discord.TextChannel):
                    channel = candidate
            if channel is None and isinstance(ctx.channel, discord.TextChannel):
                channel = ctx.channel
        if channel is None:
            await ctx.send(
                "No target channel: pass one, or set a default with "
                "`/settings config github_channel #channel`.",
                ephemeral=True,
            )
            return None
        permissions = channel.permissions_for(guild.me)
        if not permissions.send_messages or not permissions.embed_links:
            await ctx.send(
                f"I need **Send Messages** and **Embed Links** in {channel.mention}.",
                ephemeral=True,
            )
            return None
        return channel

    @commands.hybrid_group(name="github", description="GitHub org and repo watches.", invoke_without_command=True)
    async def github_group(self, ctx: commands.Context) -> None:
        await self.list_watches.callback(self, ctx)

    @github_group.command(
        name="watch",
        description="Watch an org, a repo, or a pasted GitHub URL. Org watches cover every repo.",
    )
    @app_commands.describe(
        target="`org`, `org/repo`, or `https://github.com/org/repo`.",
        channel="Where to post. Defaults to github_channel, then this channel.",
        events="Comma list: releases, pulls, issues, pushes. Default all.",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def watch(
        self,
        ctx: commands.Context,
        target: str,
        channel: discord.TextChannel | None = None,
        events: str = "all",
    ) -> None:
        await has_manage_guild(ctx)
        guild = await ensure_guild(ctx)
        await ctx.defer(ephemeral=True)
        try:
            normalised, target_type = normalise_target(target)
        except GitHubError as exc:
            await ctx.send(str(exc), ephemeral=True)
            return
        if events.strip().lower() in ("", "all"):
            event_csv = DEFAULT_EVENTS
        else:
            requested = [part.strip().lower() for part in events.split(",") if part.strip()]
            invalid = [part for part in requested if part not in EVENT_TYPES]
            if invalid:
                await ctx.send(
                    f"Unknown event type(s): {', '.join(invalid)}. "
                    f"Choose from {', '.join(EVENT_TYPES)}.",
                    ephemeral=True,
                )
                return
            event_csv = normalise_events(requested)
        target_channel = await self._target_channel(ctx, channel)
        if target_channel is None:
            return
        try:
            watch = await self.github.add_watch(
                guild_id=guild.id,
                target=normalised,
                target_type=target_type,
                channel_id=target_channel.id,
                events=event_csv,
                created_by=ctx.author.id,
            )
        except WatchLimitError as exc:
            await ctx.send(str(exc), ephemeral=True)
            return
        except GitHubError as exc:
            await ctx.send(str(exc), ephemeral=True)
            return
        embed = discord.Embed(
            title="Watching",
            description=(
                f"`{normalised}` ({'org' if target_type == 'org' else 'repo'}) → {target_channel.mention}\n"
                f"events: {', '.join(event_csv.split(','))}\n"
                "First check in 5 min · 0 items backfilled"
            ),
            color=BRAND_COLOR,
        )
        if watch.get("state") == "rate-limited":
            embed.add_field(
                name="Rate limited",
                value="The first check hit GitHub's rate limit. Notifications start when it resets.",
                inline=False,
            )
        await ctx.send(embed=embed, ephemeral=True)

    @github_group.command(name="list", description="Every watch with health and last activity.")
    async def list_watches(self, ctx: commands.Context) -> None:
        guild = await ensure_guild(ctx)
        watches = await self.github.list_watches(guild.id)
        if not watches:
            await ctx.send(
                "No GitHub watches. Add one with `/github watch appardas #dev-releases`.",
                ephemeral=True,
            )
            return
        lines = []
        for watch in watches:
            state = self.github.display_state(watch)
            channel = f"<#{watch['channel_id']}>"
            kinds = "all" if watch["events"] == DEFAULT_EVENTS else watch["events"].replace(",", " ")
            lines.append(
                f"{watch['target']} ({watch['target_type']})\n"
                f"  {channel} · {kinds} · poll {self.github.last_poll_text(watch)} · "
                f"delivered {self.github.last_delivery_text(watch)} · {state}"
            )
        embed = discord.Embed(
            title=f"{len(watches)} watch{'es' if len(watches) != 1 else ''}",
            description="```\n" + truncate("\n".join(lines), 3900) + "\n```",
            color=BRAND_COLOR,
        )
        remaining = self.github.rate_limit_remaining
        footer = f"poll every {self.bot.cfg.github_poll_interval_s // 60} min"
        if remaining is not None:
            footer += f" · rate limit {remaining} remaining"
        embed.set_footer(text=footer)
        await ctx.send(embed=embed, ephemeral=True)

    @github_group.command(name="test", description="Post the latest matching events once.")
    @app_commands.describe(target="A target that is already watched.")
    async def test(self, ctx: commands.Context, target: str) -> None:
        guild = await ensure_guild(ctx)
        try:
            normalised, _ = normalise_target(target)
        except GitHubError as exc:
            await ctx.send(str(exc), ephemeral=True)
            return
        watch = await self.github.get_watch(guild.id, normalised)
        if not watch:
            await ctx.send(
                f"`{normalised}` is not watched here. Add it with `/github watch`.",
                ephemeral=True,
            )
            return
        await ctx.defer(ephemeral=True)
        ok, detail = await self.github.test_post(self.bot, watch)
        await ctx.send(
            detail, ephemeral=True
        )

    @github_group.command(name="unwatch", description="Remove a watch. Audit history stays.")
    @app_commands.describe(target="The watched org or org/repo.")
    @app_commands.default_permissions(manage_guild=True)
    async def unwatch(self, ctx: commands.Context, target: str) -> None:
        await has_manage_guild(ctx)
        guild = await ensure_guild(ctx)
        try:
            normalised, _ = normalise_target(target)
        except GitHubError as exc:
            await ctx.send(str(exc), ephemeral=True)
            return
        removed = await self.github.remove_watch(guild.id, normalised, ctx.author.id)
        if not removed:
            await ctx.send(
                f"`{normalised}` is not watched here.", ephemeral=True
            )
            return
        await ctx.send(
            f"Removed `{normalised}`. Past deliveries remain in the audit log.", ephemeral=True
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(GithubCog(bot))
