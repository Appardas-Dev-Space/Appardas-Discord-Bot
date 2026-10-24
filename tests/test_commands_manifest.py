from __future__ import annotations

import importlib

import discord
from discord.ext import commands

from app.cogs.registry import COGS
from app.commands_docs import COMMANDS
from app.commands_export import build_manifest, collect_paths


async def _tree_bot():
    intents = discord.Intents.none()
    intents.guilds = True
    bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
    for meta in COGS.values():
        module = importlib.import_module(meta.module)
        await bot.add_cog(getattr(module, meta.class_name)(bot))
    return bot


async def test_manifest_matches_live_tree():
    bot = await _tree_bot()
    try:
        leaves, groups = collect_paths(bot.tree)
        assert groups == ["appardas", "settings", "github", "dev"]
        assert len(leaves) == len(COMMANDS)
        assert set(leaves) == set(COMMANDS)
    finally:
        await bot.close()


async def test_manifest_exports_every_cog():
    bot = await _tree_bot()
    try:
        manifest = build_manifest(bot.tree)
        assert manifest["version"]
        assert set(manifest["cogs"]) == set(COGS)
        total = sum(len(cog["commands"]) for cog in manifest["cogs"].values())
        assert total == len(COMMANDS)
        for cog in manifest["cogs"].values():
            assert cog["commands"]
    finally:
        await bot.close()


async def test_admin_commands_declare_manage_guild_permissions():
    bot = await _tree_bot()
    try:
        settings = bot.tree.get_command("settings")
        dev = bot.tree.get_command("dev")
        github = bot.tree.get_command("github")
        assert settings is not None and dev is not None and github is not None
        for name in ("cogs", "cog", "config", "audit", "prefix"):
            command = settings.get_command(name)
            assert command is not None
            assert command.default_permissions.manage_guild is True
        for name in ("announce", "schedule", "cancel"):
            command = dev.get_command(name)
            assert command is not None
            assert command.default_permissions.manage_guild is True
        for name in ("watch", "unwatch"):
            command = github.get_command(name)
            assert command is not None
            assert command.default_permissions.manage_guild is True
        for name in ("dashboard", "errors"):
            command = bot.tree.get_command(name)
            assert command is not None
            assert command.default_permissions.manage_guild is True
    finally:
        await bot.close()
