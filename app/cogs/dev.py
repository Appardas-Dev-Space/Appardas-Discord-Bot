"""`dev` cog — announcements and nudges.

Togglable. This is the cog you switch off when the DM path misbehaves:
commands refuse, the dispatcher skips due rows, and nothing is deleted.
"""

from __future__ import annotations

import io
import json
import logging

import discord
from discord import app_commands
from discord.ext import commands

from ..checks import ensure_guild, has_manage_guild
from ..services.announcements import (
    AnnouncementError,
    AnnouncementLimitError,
    local_text,
    parse_when,
)
from ..services.images import get_default_announcement_image, optimize_image
from ..services.nudge import MAX_MESSAGE, NudgeError
from ..util import ago_text, iso, remaining_text, truncate, utcnow
from ..views.embeds import BRAND_COLOR, announcement_embed

log = logging.getLogger(__name__)


class ConfirmView(discord.ui.View):
    def __init__(self, author_id: int, timeout: float = 60.0) -> None:
        super().__init__(timeout=timeout)
        self.author_id = author_id
        self.confirmed = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "This confirmation belongs to someone else.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Cancel announcement", style=discord.ButtonStyle.danger)
    async def confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        self.confirmed = True
        self.stop()
        await interaction.response.defer()


class DevCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @property
    def settings(self):
        return self.bot.settings_service  # type: ignore[attr-defined]

    @property
    def announcements(self):
        return self.bot.announcements  # type: ignore[attr-defined]

    @property
    def nudges(self):
        return self.bot.nudges  # type: ignore[attr-defined]

    # -------------------------------------------------------------- announce
    async def _announce_channel(
        self, ctx: commands.Context
    ) -> discord.TextChannel | None:
        guild = ctx.guild
        if guild is None:
            return None
        channel_id = await self.settings.get_setting(guild.id, "announce_channel")
        if not channel_id:
            await ctx.send(
                "No announcement channel is configured. Set one first:\n"
                "`/settings config announce_channel #announcements`",
                ephemeral=True,
            )
            return None
        channel = guild.get_channel(int(channel_id))
        if not isinstance(channel, discord.TextChannel):
            await ctx.send(
                "The configured announcement channel no longer exists. "
                "Set a new one with `/settings config announce_channel #channel`.",
                ephemeral=True,
            )
            return None
        permissions = channel.permissions_for(guild.me)
        missing = []
        if not permissions.send_messages:
            missing.append("Send Messages")
        if not permissions.embed_links:
            missing.append("Embed Links")
        if missing:
            await ctx.send(
                f"I am missing **{', '.join(missing)}** in {channel.mention}. "
                "Fix the channel permissions, then try again.",
                ephemeral=True,
            )
            return None
        return channel

    @commands.hybrid_group(name="dev", description="Announcements and nudges.", invoke_without_command=True)
    async def dev(self, ctx: commands.Context) -> None:
        await self.announcements_list.callback(self, ctx)

    @dev.command(
        name="announce", description="Post an announcement embed to the configured channel."
    )
    @app_commands.describe(
        title="Embed title (max 256 characters).",
        body="Embed body (max 4000 characters).",
        ping_role="Optional role to ping with the announcement.",
        image="Optional image attachment for the announcement.",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def announce(
        self,
        ctx: commands.Context,
        title: str,
        body: str,
        ping_role: discord.Role | None = None,
        image: discord.Attachment | None = None,
    ) -> None:
        await has_manage_guild(ctx)
        guild = await ensure_guild(ctx)
        if len(title) > 256 or len(body) > 4000:
            await ctx.send(
                "Title must be ≤ 256 characters and body ≤ 4000.", ephemeral=True
            )
            return
        channel = await self._announce_channel(ctx)
        if channel is None:
            return
        mentions = discord.AllowedMentions.none()
        content = None
        if ping_role is not None:
            if not ping_role.mentionable:
                permissions = channel.permissions_for(guild.me)
                if not permissions.mention_everyone:
                    await ctx.send(
                        f"{ping_role.mention} is not mentionable and I lack **Mention Everyone** "
                        "in that channel. Make the role mentionable or drop the ping.",
                        ephemeral=True,
                    )
                    return
            content = ping_role.mention
            mentions = discord.AllowedMentions(roles=True)

        await ctx.defer(ephemeral=True)
        target_attachment = image or (
            ctx.message.attachments[0]
            if getattr(ctx, "message", None) and ctx.message.attachments
            else None
        )
        if target_attachment is not None:
            raw_bytes = await target_attachment.read()
            image_bytes, ext = optimize_image(raw_bytes)
        else:
            image_bytes, ext = get_default_announcement_image()

        filename = f"announcement.{ext}"
        file = discord.File(io.BytesIO(image_bytes), filename=filename) if image_bytes else None
        author_icon = (
            ctx.author.display_avatar.url
            if hasattr(ctx.author, "display_avatar")
            else None
        )
        embed = announcement_embed(
            title,
            body,
            author_name=ctx.author.display_name,
            author_icon_url=author_icon,
            image_url=f"attachment://{filename}" if file else None,
        )
        try:
            send_kwargs: dict[str, Any] = {
                "content": content,
                "embed": embed,
                "allowed_mentions": mentions,
            }
            if file:
                send_kwargs["file"] = file
            message = await channel.send(**send_kwargs)
        except discord.Forbidden:
            await ctx.send(
                f"Discord refused the post in {channel.mention}. "
                "Check Send Messages and Embed Links.",
                ephemeral=True,
            )
            return
        await self.bot.audit.record(
            guild_id=guild.id,
            actor_id=ctx.author.id,
            action="announce.send",
            target=str(message.id),
            detail={
                "channel_id": channel.id,
                "title": truncate(title, 256),
                "source": "bot",
            },
        )
        await ctx.send(
            f"Posted in {channel.mention}.\n{message.jump_url}", ephemeral=True
        )

    @dev.command(
        name="schedule",
        description="Schedule the same embed. Accepts `in 2h`, `tomorrow 09:00`, `mon 09:00`.",
    )
    @app_commands.describe(
        when="When to send: `in 2h`, `tomorrow 09:00`, `mon 09:00`, `2026-11-01 18:00`.",
        title="Embed title (max 256).",
        body="Embed body (max 4000).",
        ping_role="Optional role to ping.",
        image="Optional image attachment for the announcement.",
    )
    @app_commands.default_permissions(manage_guild=True)
    async def schedule(
        self,
        ctx: commands.Context,
        when: str,
        title: str,
        body: str,
        ping_role: discord.Role | None = None,
        image: discord.Attachment | None = None,
    ) -> None:
        await has_manage_guild(ctx)
        guild = await ensure_guild(ctx)
        if len(title) > 256 or len(body) > 4000:
            await ctx.send(
                "Title must be ≤ 256 characters and body ≤ 4000.", ephemeral=True
            )
            return
        channel = await self._announce_channel(ctx)
        if channel is None:
            return
        tz = await self.settings.timezone(guild.id)
        try:
            send_at = parse_when(when, tz)
        except AnnouncementError as exc:
            await ctx.send(str(exc), ephemeral=True)
            return
        target_attachment = image or (
            ctx.message.attachments[0]
            if getattr(ctx, "message", None) and ctx.message.attachments
            else None
        )
        image_url = target_attachment.url if target_attachment else None
        author_icon = (
            ctx.author.display_avatar.url
            if hasattr(ctx.author, "display_avatar")
            else None
        )
        try:
            announcement_id = await self.announcements.create_scheduled(
                guild_id=guild.id,
                channel_id=channel.id,
                author_id=ctx.author.id,
                title=title,
                body=body,
                ping_role_id=ping_role.id if ping_role else None,
                send_at=send_at,
                image_url=image_url,
                author_icon_url=author_icon,
            )
        except AnnouncementLimitError as exc:
            await ctx.send(str(exc), ephemeral=True)
            return
        seconds = max(0, (send_at - utcnow()).total_seconds())
        embed = discord.Embed(
            title="Scheduled",
            description=(
                f"`#{announcement_id}` will post in {channel.mention}.\n\n"
                f"**{local_text(send_at, tz)}**\n"
                f"in {remaining_text(seconds)} · `{iso(send_at)}`"
            ),
            color=BRAND_COLOR,
        )
        embed.set_footer(text="Pending rows live in SQLite and survive a restart.")
        await ctx.send(embed=embed, ephemeral=True)

    @dev.command(name="announcements", description="The next five pending announcements.")
    async def announcements_list(self, ctx: commands.Context) -> None:
        guild = await ensure_guild(ctx)
        rows = await self.announcements.list_pending(guild.id, limit=5)
        total = await self.announcements.pending_count(guild.id)
        if not rows:
            await ctx.send(
                "Nothing is scheduled. `/dev schedule` adds one.", ephemeral=True
            )
            return
        tz = await self.settings.timezone(guild.id)
        lines = []
        for row in rows:
            payload = json.loads(row["payload"])
            from ..util import parse_iso

            local = local_text(parse_iso(row["send_at"]), tz)
            lines.append(
                f"#{row['id']:<3} {local} · {truncate(payload.get('title', '—'), 50)}"
            )
        embed = discord.Embed(
            title=f"{total} scheduled",
            description="```\n" + "\n".join(lines) + "\n```",
            color=BRAND_COLOR,
        )
        embed.set_footer(text="Cancel with /dev cancel <id>.")
        await ctx.send(embed=embed, ephemeral=True)

    @dev.command(name="cancel", description="Cancel one pending announcement.")
    @app_commands.describe(id="The announcement id from /dev announcements.")
    @app_commands.default_permissions(manage_guild=True)
    async def cancel(self, ctx: commands.Context, id: int) -> None:
        await has_manage_guild(ctx)
        guild = await ensure_guild(ctx)
        row = await self.bot.db.fetchone(
            "SELECT id, payload, send_at, status FROM announcements WHERE id = ? AND guild_id = ?",
            (id, guild.id),
        )
        if not row:
            await ctx.send(
                f"No announcement `#{id}` in this server.", ephemeral=True
            )
            return
        if row["status"] != "pending":
            await ctx.send(
                f"`#{id}` is already **{row['status']}** — only pending announcements can be cancelled.",
                ephemeral=True,
            )
            return
        payload = json.loads(row["payload"])
        view = ConfirmView(ctx.author.id)
        reply = await ctx.send(
            f"Cancel `#{id}` — “{truncate(payload.get('title', '—'), 100)}”?",
            view=view,
            ephemeral=True,
        )
        await view.wait()
        if view.confirmed:
            # Recheck the author after the potentially minute-long button wait.
            await has_manage_guild(ctx)
            ok = await self.announcements.cancel(guild.id, id, ctx.author.id)
            await reply.edit(
                content=f"Cancelled `#{id}`." if ok else "It changed before the confirmation — nothing to cancel.",
                view=None,
            )
        else:
            await reply.edit(
                content="Left it alone — nothing changed.", view=None
            )

    # ----------------------------------------------------------------- nudge
    @dev.command(name="nudge", description="Send one private DM on your behalf.")
    @app_commands.describe(
        member="Who to nudge.",
        message=f"What to say (max {MAX_MESSAGE} characters).",
    )
    async def nudge(
        self,
        ctx: commands.Context,
        member: discord.Member,
        *,
        message: str,
    ) -> None:
        guild = await ensure_guild(ctx)
        await ctx.defer(ephemeral=True)
        try:
            result = await self.nudges.send(
                guild=guild, author=ctx.author, target=member, message=message
            )
        except NudgeError as exc:
            await ctx.send(str(exc), ephemeral=True)
            return
        if result.delivered:
            await ctx.send(
                f"Delivered to {member.mention}.", ephemeral=True
            )
        else:
            await ctx.send(result.detail, ephemeral=True)

    @dev.command(name="optout", description="Stop receiving nudges from anyone.")
    async def optout(self, ctx: commands.Context) -> None:
        await ensure_guild(ctx)
        await self.nudges.opt_out(ctx.author.id)
        await self.bot.audit.record(
            guild_id=ctx.guild.id,
            actor_id=ctx.author.id,
            action="nudge.optout",
            target=str(ctx.author.id),
            detail={"source": "bot"},
        )
        await ctx.send(
            "You will not receive nudges anymore. `/dev optin` reverses this.", ephemeral=True
        )

    @dev.command(name="optin", description="Allow nudges again.")
    async def optin(self, ctx: commands.Context) -> None:
        await ensure_guild(ctx)
        await self.nudges.opt_in(ctx.author.id)
        await self.bot.audit.record(
            guild_id=ctx.guild.id,
            actor_id=ctx.author.id,
            action="nudge.optin",
            target=str(ctx.author.id),
            detail={"source": "bot"},
        )
        await ctx.send("Nudges are allowed again.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(DevCog(bot))
