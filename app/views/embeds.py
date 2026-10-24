"""Embed builders. Brand amber on every card; text caps enforced upstream."""

from __future__ import annotations

from datetime import datetime
from typing import Iterable

import discord

from ..util import truncate

BRAND_COLOR = 0xD97706  # amber-600
MUTED_COLOR = 0x78716C
BAD_COLOR = 0xE5566F


def _footer(embed: discord.Embed, text: str) -> discord.Embed:
    embed.set_footer(text=text)
    return embed


def announcement_embed(
    title: str,
    body: str,
    *,
    author_name: str | None = None,
    author_icon_url: str | None = None,
    scheduled_for: datetime | None = None,
    image_url: str | None = None,
) -> discord.Embed:
    clean_title = (title or "").strip()
    if not clean_title or clean_title.upper() in ("ANNOUNCEMENT", "SERVER ANNOUNCEMENT"):
        header = "SERVER ANNOUNCEMENT"
    elif not clean_title.upper().startswith("ANNOUNCEMENT") and not clean_title.upper().startswith("SERVER ANNOUNCEMENT"):
        header = f"SERVER ANNOUNCEMENT · {clean_title}"
    else:
        header = clean_title

    embed = discord.Embed(
        title=truncate(header, 256),
        description=truncate(body, 4000),
        color=BRAND_COLOR,
        timestamp=scheduled_for,
    )
    embed.set_author(name="Admin Announcement", icon_url=author_icon_url)
    if author_icon_url:
        embed.set_thumbnail(url=author_icon_url)
    embed.set_footer(text="Message your `admin` to learn more")
    if image_url:
        embed.set_image(url=image_url)
    return embed


def nudge_embed(author_name: str, guild_name: str, message: str) -> discord.Embed:
    embed = discord.Embed(
        title=f"Nudge from {author_name}",
        description=truncate(message, 500),
        color=BRAND_COLOR,
    )
    embed.add_field(name="Server", value=truncate(guild_name, 100), inline=True)
    return _footer(embed, "Reply in the server · /dev optout to stop nudges")


def github_summary_embed(
    target: str,
    target_type: str,
    total: int,
    sections: Iterable[tuple[str, list[str]]],
) -> discord.Embed:
    label = "org" if target_type == "org" else "repo"
    embed = discord.Embed(
        title=truncate(f"📦 {target} · {total} new update{'s' if total != 1 else ''}", 256),
        color=BRAND_COLOR,
    )
    description_parts: list[str] = []
    for heading, lines in sections:
        if not lines:
            continue
        description_parts.append(f"**{heading}**\n" + "\n".join(lines))
    embed.description = truncate("\n\n".join(description_parts), 4000) or None
    return _footer(embed, f"appardas · github · {label} watch")


def github_event_embed(
    title: str,
    url: str,
    *,
    target: str,
    detail: str | None = None,
    release_notes: str | None = None,
) -> discord.Embed:
    embed = discord.Embed(
        title=truncate(title, 256),
        url=url,
        color=BRAND_COLOR,
        description=truncate(release_notes, 300) if release_notes else None,
    )
    if detail:
        embed.add_field(name="Detail", value=truncate(detail, 500), inline=False)
    return _footer(embed, f"appardas · github · {target}")
