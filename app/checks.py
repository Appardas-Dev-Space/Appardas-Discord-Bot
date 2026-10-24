"""Shared checks for slash interactions and hybrid command contexts."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from .cogs.registry import class_key


CommandSource = discord.Interaction | commands.Context


def _client(source: CommandSource):
    return source.bot if isinstance(source, commands.Context) else source.client


def _user(source: CommandSource):
    return source.author if isinstance(source, commands.Context) else source.user


def _guild_id(source: CommandSource) -> int | None:
    return source.guild.id if source.guild is not None else None


def _failure(source: CommandSource, message: str):
    error = commands.CheckFailure if isinstance(source, commands.Context) else app_commands.CheckFailure
    return error(message)


def _service(source: CommandSource, name: str):
    return getattr(_client(source), name, None)


async def guild_only(interaction: CommandSource) -> bool:
    if _guild_id(interaction) is None:
        raise _failure(interaction, "This command only works inside a server.")
    return True


async def cog_enabled_check(interaction: CommandSource) -> bool:
    """The authoritative gate: a disabled cog refuses, instantly, with its reason."""
    guild_id = _guild_id(interaction)
    if guild_id is None:
        return True
    command = interaction.command
    if command is None:
        return True
    key = class_key(getattr(command, "cog", None) or getattr(command, "binding", None))
    if key is None:
        return True
    settings_service = _service(interaction, "settings_service")
    if settings_service is None:
        return True
    state = await settings_service.cog_state(guild_id, key)
    if state.state == "faulted":
        raise _failure(
            interaction,
            f"`{key}` is temporarily unavailable — it failed to load. "
            "An operator can run `/appardas reload " + key + "` after checking `/errors`."
        )
    if not state.enabled:
        message = f"`{key}` is offline for maintenance."
        if state.reason:
            message += f"\nReason: {state.reason}"
        raise _failure(interaction, message)
    return True


async def global_check(interaction: CommandSource) -> bool:
    await guild_only(interaction)
    return await cog_enabled_check(interaction)


async def is_owner(interaction: CommandSource) -> bool:
    config = getattr(_client(interaction), "cfg", None)
    owner_ids = getattr(config, "owner_ids", frozenset())
    if _user(interaction).id in owner_ids:
        return True
    raise _failure(interaction, "Only the bot owner can run this command.")


async def has_manage_guild(interaction: CommandSource) -> bool:
    config = getattr(_client(interaction), "cfg", None)
    user = _user(interaction)
    if user.id in getattr(config, "owner_ids", frozenset()):
        return True
    guild = interaction.guild
    if guild is None:
        raise _failure(interaction, "This command only works inside a server.")
    # Re-fetch the member instead of trusting the interaction's cached view:
    # permissions can change between the command being rendered and invoked.
    member: discord.Member | None = None
    try:
        member = await guild.fetch_member(user.id)
    except Exception:
        # The gateway cache is the fallback when the REST call fails; the
        # permission bit is still read from a real Member object.
        member = guild.get_member(user.id)  # type: ignore[assignment]
    permissions = getattr(member, "guild_permissions", None)
    if permissions is not None and permissions.manage_guild:
        return True
    raise _failure(interaction, "You need **Manage Server** to use this command.")


async def ensure_guild(interaction: CommandSource) -> discord.Guild:
    if interaction.guild is None:
        raise _failure(interaction, "This command only works inside a server.")
    settings_service = _service(interaction, "settings_service")
    if settings_service is not None:
        await settings_service.ensure_guild(interaction.guild.id, interaction.guild.name)
    return interaction.guild
