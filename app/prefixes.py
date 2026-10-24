"""Validation and per-guild text prefixes; Discord's slash prefix stays fixed."""

from __future__ import annotations

import unicodedata

import discord
from discord.ext import commands

MAX_PREFIX_LENGTH = 16


def validate_prefix(value: object) -> str | None:
    """None means mention-only. Never allow an empty, invisible or slash prefix."""
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= MAX_PREFIX_LENGTH
        or any(char.isspace() or unicodedata.category(char).startswith("C") for char in value)
    ):
        raise ValueError("The prefix must be 1–16 visible characters with no spaces or control characters.")
    if value.startswith("/"):
        raise ValueError("Discord reserves `/` for slash commands. Choose a text prefix such as `!` or `?`.")
    if "<@" in value or "<#" in value or "@everyone" in value or "@here" in value:
        raise ValueError("Use a text prefix, not a Discord mention. Bot mentions always work separately.")
    return value


async def resolve_prefix(bot: commands.Bot, message: discord.Message) -> list[str]:
    """Keep mentions as a recovery path, even before settings are available."""
    mentions = commands.when_mentioned(bot, message)
    config = getattr(bot, "cfg", None)
    settings = getattr(bot, "settings_service", None)
    has_content_intent = bool(getattr(getattr(bot, "intents", None), "message_content", False) or getattr(config, "enable_message_content_intent", False))
    if message.guild is None or settings is None or not has_content_intent:
        return mentions
    prefix = await settings.command_prefix(message.guild.id)
    return [*mentions, prefix] if prefix is not None else mentions
