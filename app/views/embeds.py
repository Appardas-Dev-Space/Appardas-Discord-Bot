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
    footer_text: str | None = None,
    fields: list[dict[str, Any]] | None = None,
    color: int | None = None,
) -> discord.Embed:
    clean_title = (title or "").strip()
    if not clean_title or clean_title.upper() in ("ANNOUNCEMENT", "SERVER ANNOUNCEMENT"):
        header = "SERVER ANNOUNCEMENT"
    elif not clean_title.upper().startswith("ANNOUNCEMENT") and not clean_title.upper().startswith("SERVER ANNOUNCEMENT"):
        header = f"SERVER ANNOUNCEMENT · {clean_title}"
    else:
        header = clean_title

    embed_color = BRAND_COLOR
    if color is not None:
        if isinstance(color, int):
            embed_color = color
        elif isinstance(color, str):
            clean = color.strip().lstrip("#")
            try:
                embed_color = int(clean, 16)
            except ValueError:
                embed_color = BRAND_COLOR

    embed = discord.Embed(
        title=truncate(header, 256),
        description=truncate(body, 4000),
        color=embed_color,
        timestamp=scheduled_for,
    )
    author_display = author_name or "Admin Announcement"
    embed.set_author(name=truncate(author_display, 256), icon_url=author_icon_url)
    if author_icon_url:
        embed.set_thumbnail(url=author_icon_url)
    footer = footer_text if footer_text is not None and footer_text.strip() else "Message your `admin` to learn more"
    embed.set_footer(text=truncate(footer, 2048))
    if image_url:
        embed.set_image(url=image_url)
    if fields:
        for f in fields:
            name = str(f.get("name") or "").strip()
            val = str(f.get("value") or "").strip()
            if name and val:
                embed.add_field(
                    name=truncate(name, 256),
                    value=truncate(val, 1024),
                    inline=bool(f.get("inline", False)),
                )
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
