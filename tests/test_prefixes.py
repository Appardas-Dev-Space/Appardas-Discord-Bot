from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import discord
import pytest
from discord import app_commands
from discord.ext import commands

from app.checks import global_check, has_manage_guild
from app.cogs.registry import COGS
from app.main import AppBot
from app.prefixes import validate_prefix


@pytest.mark.parametrize("value", [None, "!", "??", "app!", "é", "*", "x" * 16])
def test_valid_prefix(value):
    assert validate_prefix(value) == value


@pytest.mark.parametrize("value", ["", " ", "a b", " !", "!\t", "!\n", "\x00", "\u200b", "x" * 17, "/", "/!", "<@42>", "<@!42>", "<@&42>", "<#42>", "@everyone", "@here", True, 17, []])
def test_unsafe_prefix_is_rejected(value):
    with pytest.raises(ValueError):
        validate_prefix(value)


@pytest.fixture
async def command_bot(cfg, db, audit, settings_service, nudges, announcements):
    # Register the real cogs, without logging in, starting loops or HTTP clients.
    async with AppBot(replace(cfg, enable_message_content_intent=True)) as bot:
        bot._connection.user = discord.ClientUser(
            state=bot._connection,
            data={"id": "99", "username": "appardas", "discriminator": "0", "avatar": None, "bot": True},
        )
        bot.db = db
        bot.audit = audit
        bot.settings_service = settings_service
        bot.nudges = nudges
        bot.announcements = announcements
        bot.status_service = SimpleNamespace(snapshot=AsyncMock(return_value={}), mark_loop_error=Mock(), set_rest_latency=Mock())
        bot.github = SimpleNamespace(list_watches=AsyncMock(return_value=[]), rate_limit_remaining=None, watch_count=AsyncMock(return_value=0))
        bot.dispatch = Mock()
        bot.tree.interaction_check = global_check
        bot.tree.on_error = bot._on_tree_error
        await bot._load_cogs()
        assert not bot.cog_faults
        yield bot


def message(bot, content, *, guild_id=1, user_id=1, manage_guild=False, author_is_bot=False):
    author = SimpleNamespace(id=user_id, bot=author_is_bot, display_name="Tester", guild_permissions=SimpleNamespace(manage_guild=manage_guild))
    guild = None if guild_id is None else SimpleNamespace(
        id=guild_id,
        name="Test Guild",
        me=bot.user,
        fetch_member=AsyncMock(return_value=author),
        get_member=Mock(return_value=author),
    )
    return SimpleNamespace(content=content, author=author, guild=guild, channel=SimpleNamespace(id=10, type=discord.ChannelType.text), _state=bot._connection, attachments=[], mentions=[])


async def context(bot, content, **kwargs):
    ctx = await bot.get_context(message(bot, content, **kwargs))
    ctx.send = AsyncMock(return_value=SimpleNamespace(edit=AsyncMock()))
    ctx.defer = AsyncMock()
    return ctx


async def invoke_text(bot, content, **kwargs):
    ctx = await context(bot, content, **kwargs)
    assert ctx.command is not None
    await ctx.command.invoke(ctx)
    return ctx


async def invoke_slash(bot, monkeypatch, path, options, **kwargs):
    ctx = await context(bot, "<@99> help", **kwargs)
    command = bot.get_command(path)
    ctx.command = command
    ctx.prefix = "/"
    interaction = SimpleNamespace(client=bot, command=command.app_command, command_failed=False, namespace=SimpleNamespace(**options))
    ctx.interaction = interaction
    with monkeypatch.context() as patch:
        patch.setattr(bot, "get_context", AsyncMock(return_value=ctx))
        await command.app_command._invoke_with_namespace(interaction, interaction.namespace)
    return ctx


def slash_interaction(bot, monkeypatch, path, options, *, user_id=2, manage_guild=False):
    # Exercise Discord's payload, namespace and synthetic Context conversion;
    # only REST member lookup and outgoing response transport are mocked.
    guild = discord.Guild(
        state=bot._connection,
        data={
            "id": "1", "name": "Test Guild", "owner_id": "4",
            "roles": [
                {"id": "1", "name": "@everyone", "permissions": str(discord.Permissions(view_channel=True, send_messages=True, embed_links=True).value)},
                {"id": "5", "name": "Admin", "permissions": str(discord.Permissions(manage_guild=True).value)},
            ],
            "channels": [{"id": "10", "name": "general", "type": 0, "position": 0, "permission_overwrites": []}],
        },
    )
    bot._connection._add_guild(guild)
    command = bot.get_command(path)
    parameters = {parameter.name: parameter for parameter in command.app_command.parameters}
    command_options = [{"name": key, "type": parameters[key].type.value, "value": value} for key, value in options.items()]
    parts = path.split()
    for index in range(len(parts) - 1, 0, -1):
        command_options = [{"name": parts[index], "type": 1 if index == len(parts) - 1 else 2, "options": command_options}]
    interaction = discord.Interaction(
        state=bot._connection,
        data={
            "id": str(discord.utils.time_snowflake(discord.utils.utcnow())),
            "application_id": "99", "type": 2, "token": "test-token", "version": 1,
            "guild_id": "1", "channel": {"id": "10", "type": 0}, "attachment_size_limit": 10485760,
            "member": {
                "user": {"id": str(user_id), "username": "Tester", "discriminator": "0", "avatar": None, "bot": False},
                "roles": ["5"] if manage_guild else [], "flags": 0,
            },
            "data": {"id": "123", "type": 1, "name": parts[0], "options": command_options},
        },
    )
    guild._add_member(interaction.user)
    monkeypatch.setattr(discord.Guild, "fetch_member", AsyncMock(return_value=interaction.user))
    reply = SimpleNamespace(edit=AsyncMock())
    response = SimpleNamespace(is_done=Mock(return_value=False))

    async def send_response(*args, **kwargs):
        response.is_done.return_value = True
        return SimpleNamespace(resource=None)

    async def defer_response(**kwargs):
        response.is_done.return_value = True

    response.send_message = AsyncMock(side_effect=send_response)
    response.defer = AsyncMock(side_effect=defer_response)
    interaction._cs_response = response
    interaction._cs_followup = SimpleNamespace(send=AsyncMock(return_value=reply))
    interaction._original_response = reply
    return interaction


async def test_prefix_can_be_bootstrapped_by_mention_then_changed_and_reset(command_bot):
    bot = command_bot
    ctx = await invoke_text(bot, "<@99> settings prefix !")
    assert "effective immediately" in ctx.send.call_args.args[0]
    assert (await context(bot, "!help")).command.name == "help"
    ctx = await invoke_text(bot, "!settings prefix ??")
    assert ctx.command.qualified_name == "settings prefix"
    assert (await context(bot, "!help")).command is None
    assert (await context(bot, "??help")).command.name == "help"
    assert (await context(bot, "<@!99> help")).command.name == "help"
    await invoke_text(bot, "??settings prefix reset")
    assert (await context(bot, "??help")).command is None
    assert (await context(bot, "<@99> help")).command.name == "help"
    await invoke_text(bot, "<@99> settings prefix !")
    assert (await context(bot, "!help")).command.name == "help"
    await invoke_text(bot, "!settings prefix clear")
    assert (await context(bot, "!help")).command is None
    await invoke_text(bot, "<@99> settings prefix ??")
    assert (await context(bot, "??help")).command.name == "help"
    await invoke_text(bot, "??settings prefix none")
    assert (await context(bot, "??help")).command is None


async def test_default_stays_mention_only_and_prefix_is_server_specific(command_bot):
    bot = command_bot
    assert await bot.get_prefix(message(bot, "!help")) == ["<@99> ", "<@!99> "]
    await bot.settings_service.update_setting(1, "prefix", "!!", 1)
    assert (await context(bot, "!! help")).command.name == "help"
    assert (await context(bot, "!!help", guild_id=2)).command is None
    assert await bot.get_prefix(message(bot, "!!help", guild_id=None)) == ["<@99> ", "<@!99> "]


async def test_prefix_reads_use_the_settings_cache(command_bot, monkeypatch):
    bot = command_bot
    await bot.settings_service.update_setting(1, "prefix", "!", 1)
    await bot.get_prefix(message(bot, "!help"))
    read = AsyncMock(wraps=bot.db.fetchone)
    write = AsyncMock(wraps=bot.db.execute)
    monkeypatch.setattr(bot.db, "fetchone", read)
    monkeypatch.setattr(bot.db, "execute", write)
    for _ in range(3):
        assert "!" in await bot.get_prefix(message(bot, "ordinary chat"))
    read.assert_not_awaited()
    write.assert_not_awaited()


@pytest.mark.parametrize("content", ["settings prefix !", "settings config prefix !", "settings config timezone UTC", "settings cog dev off", "settings cogs", "settings audit", "github unwatch appardas", "dev cancel 1", "status true", "errors"])
async def test_text_admin_commands_do_not_bypass_permissions(command_bot, content):
    ctx = await context(command_bot, "<@99> " + content, user_id=2)
    with pytest.raises(commands.CheckFailure, match="Manage Server"):
        await ctx.command.invoke(ctx)
    assert await command_bot.settings_service.command_prefix(1) is None
    assert await command_bot.db.scalar("SELECT COUNT(*) FROM audit_log") == 0


async def test_manage_server_admin_can_set_prefix_without_owner_bypass(command_bot):
    ctx = await invoke_text(command_bot, "<@99> settings prefix !", user_id=2, manage_guild=True)
    ctx.guild.fetch_member.assert_awaited_with(2)
    assert await command_bot.settings_service.command_prefix(1) == "!"
    await invoke_text(command_bot, "!settings config prefix ?", user_id=2, manage_guild=True)
    assert await command_bot.settings_service.command_prefix(1) == "?"


async def test_fresh_permissions_take_precedence_over_message_author(command_bot):
    ctx = await context(command_bot, "<@99> settings prefix !", user_id=2, manage_guild=True)
    ctx.guild.fetch_member.return_value = SimpleNamespace(guild_permissions=SimpleNamespace(manage_guild=False))
    with pytest.raises(commands.CheckFailure, match="Manage Server"):
        await has_manage_guild(ctx)


async def test_permission_failure_uses_member_cache_when_rest_fails(command_bot):
    ctx = await context(command_bot, "<@99> settings prefix !", user_id=2)
    ctx.guild.fetch_member.side_effect = RuntimeError("offline")
    with pytest.raises(commands.CheckFailure, match="Manage Server"):
        await has_manage_guild(ctx)
    ctx.guild.get_member.assert_called_once_with(2)


async def test_text_cog_gates_refuse_disabled_and_faulted_commands(command_bot):
    bot = command_bot
    await bot.settings_service.set_cog_state(1, "dev", False, 1, "maintenance")
    ctx = await context(bot, "<@99> dev optout")
    with pytest.raises(commands.CheckFailure, match="maintenance"):
        await ctx.command.invoke(ctx)
    assert await bot.nudges.is_opted_out(1) is False
    bot.settings_service.set_fault("github", "ImportError")
    ctx = await context(bot, "<@99> github list")
    with pytest.raises(commands.CheckFailure, match="temporarily unavailable"):
        await ctx.command.invoke(ctx)
    bot.github.list_watches.assert_not_awaited()


async def test_dms_cannot_invoke_guild_commands(command_bot):
    ctx = await context(command_bot, "<@99> settings prefix !", guild_id=None)
    with pytest.raises(commands.CheckFailure, match="inside a server"):
        await global_check(ctx)


async def test_bot_authored_messages_are_ignored(command_bot, monkeypatch):
    get_context = AsyncMock()
    monkeypatch.setattr(command_bot, "get_context", get_context)
    await command_bot.process_commands(message(command_bot, "<@99> help", author_is_bot=True))
    get_context.assert_not_awaited()


async def test_custom_prefix_requires_intent_but_read_and_reset_still_work(cfg, settings_service, audit):
    async with AppBot(cfg) as bot:
        bot._connection.user = SimpleNamespace(id=99)
        bot.settings_service = settings_service
        bot.audit = audit
        await bot._load_cogs()
        assert bot.intents.guild_messages is True
        assert bot.intents.message_content is False
        await settings_service.update_setting(1, "prefix", "!", 1)
        assert (await context(bot, "!help")).command is None
        ctx = await invoke_text(bot, "<@99> settings prefix ?")
        assert "ENABLE_MESSAGE_CONTENT_INTENT=true" in ctx.send.call_args.args[0]
        assert await settings_service.command_prefix(1) == "!"
        ctx = await invoke_text(bot, "<@99> settings prefix")
        assert "inactive" in ctx.send.call_args.args[0]
        await invoke_text(bot, "<@99> settings prefix reset")
        assert await settings_service.command_prefix(1) is None


async def test_unsafe_prefix_error_is_user_facing_and_does_not_write(command_bot):
    ctx = await invoke_text(command_bot, "<@99> settings prefix /")
    assert "reserves" in ctx.send.call_args.args[0]
    assert await command_bot.db.scalar("SELECT COUNT(*) FROM audit_log") == 0


async def test_hybrid_slash_prefix_and_config_keep_shared_validation(command_bot, monkeypatch):
    bot = command_bot
    for path, options in [("settings prefix", {"value": "!"}), ("settings config", {"key": "prefix", "value": "??"})]:
        ctx = await invoke_slash(bot, monkeypatch, path, options, user_id=2, manage_guild=True)
        assert ctx.command_failed is False
        assert await bot.settings_service.command_prefix(1) == options["value"]
        assert ctx.send.call_args.kwargs["ephemeral"] is True


async def test_hybrid_slash_prefix_rejects_unauthorized_users(command_bot, monkeypatch):
    ctx = await invoke_slash(command_bot, monkeypatch, "settings prefix", {"value": "!"}, user_id=2)
    assert ctx.command_failed is True
    assert await command_bot.settings_service.command_prefix(1) is None
    error_ctx, error = command_bot.dispatch.call_args.args[1:]
    await command_bot.on_command_error(error_ctx, error)
    assert "Manage Server" in ctx.send.call_args.args[0]


async def test_hybrid_slash_command_checks_disabled_cogs(command_bot, monkeypatch):
    await command_bot.settings_service.set_cog_state(1, "dev", False, 1, "maintenance")
    ctx = await invoke_slash(command_bot, monkeypatch, "dev optout", {})
    assert ctx.command_failed is True
    assert await command_bot.nudges.is_opted_out(1) is False


@pytest.mark.parametrize("path,options", [
    ("settings prefix", {"value": "!"}),
    ("settings config", {"key": "prefix", "value": "??"}),
    ("help", {"cog": "settings"}),
    ("settings cog", {"name": "dev", "state": "off"}),
    ("settings audit", {"limit": 3}),
    ("status", {"errors": True}),
    ("dev schedule", {"when": "in 2h", "title": "Maintenance", "body": "See you soon"}),
])
async def test_real_slash_payload_keeps_hybrid_conversion_and_ephemeral_replies(command_bot, monkeypatch, path, options):
    bot = command_bot
    if path == "dev schedule":
        await bot.settings_service.update_setting(1, "announce_channel", 10, 1)
    interaction = slash_interaction(bot, monkeypatch, path, options, manage_guild=True)
    await bot.tree._call(interaction)
    ctx = interaction._baton
    assert isinstance(ctx, commands.Context)
    assert isinstance(ctx.message, discord.Message)
    assert isinstance(ctx.author, discord.Member)
    assert ctx.author is interaction.user
    assert ctx.guild is interaction.guild
    assert ctx.prefix == "/"
    assert ctx.command is bot.get_command(path)
    assert interaction.command_failed is False
    assert interaction.response.send_message.call_args.kwargs["ephemeral"] is True
    if path in ("settings prefix", "settings config"):
        assert await bot.settings_service.command_prefix(1) == options["value"]
    if path == "dev schedule":
        assert await bot.announcements.pending_count(1) == 1


async def test_real_slash_payload_cannot_bypass_prefix_permissions(command_bot, monkeypatch):
    bot = command_bot
    interaction = slash_interaction(bot, monkeypatch, "settings prefix", {"value": "!"})
    await bot.tree._call(interaction)
    assert interaction.command_failed is True
    error_ctx, error = bot.dispatch.call_args.args[1:]
    await bot.on_command_error(error_ctx, error)
    reply = interaction.response.send_message.call_args.kwargs
    assert "Manage Server" in reply["content"]
    assert reply["ephemeral"] is True
    assert await bot.settings_service.command_prefix(1) is None
    assert await bot.db.scalar("SELECT COUNT(*) FROM audit_log") == 0


async def test_real_slash_payload_is_gated_before_disabled_cog_runs(command_bot, monkeypatch):
    bot = command_bot
    await bot.settings_service.set_cog_state(1, "dev", False, 1, "maintenance")
    interaction = slash_interaction(bot, monkeypatch, "dev optout", {})
    with pytest.raises(app_commands.CheckFailure) as excinfo:
        await bot.tree._call(interaction)
    await bot.tree._dispatch_error(interaction, excinfo.value)
    assert interaction.command_failed is True
    assert "maintenance" in interaction.response.send_message.call_args.args[0]
    assert await bot.nudges.is_opted_out(2) is False


@pytest.mark.parametrize("path,options", [("help", {"cog": "settings"}), ("about", {}), ("invite", {}), ("status", {}), ("errors", {}), ("settings config", {"key": "timezone", "value": "Europe/Berlin"}), ("settings cogs", {}), ("settings cog", {"name": "dev", "state": "off"}), ("settings audit", {}), ("github list", {}), ("dev announcements", {}), ("dev optout", {}), ("dev optin", {})])
async def test_existing_slash_commands_still_execute(command_bot, monkeypatch, path, options):
    ctx = await invoke_slash(command_bot, monkeypatch, path, options)
    assert ctx.command_failed is False
    ctx.send.assert_awaited()


async def test_text_schedule_parses_quoted_arguments_and_persists(command_bot, monkeypatch):
    bot = command_bot
    channel = SimpleNamespace(id=10, mention="<#10>")
    monkeypatch.setattr(bot.get_cog("DevCog"), "_announce_channel", AsyncMock(return_value=channel))
    await bot.settings_service.update_setting(1, "prefix", "!", 1)
    ctx = await invoke_text(bot, '!dev schedule "in 2h" "Maintenance window" "Back at 22:00 UTC."')
    row = await bot.db.fetchone("SELECT payload, channel_id, status FROM announcements")
    assert json.loads(row["payload"])["title"] == "Maintenance window"
    assert json.loads(row["payload"])["body"] == "Back at 22:00 UTC."
    assert row["channel_id"] == 10
    assert row["status"] == "pending"
    assert ctx.send.call_args.kwargs["embed"].title == "Scheduled"


async def test_hybrid_slash_schedule_uses_the_same_service(command_bot, monkeypatch):
    bot = command_bot
    monkeypatch.setattr(bot.get_cog("DevCog"), "_announce_channel", AsyncMock(return_value=SimpleNamespace(id=10, mention="<#10>")))
    ctx = await invoke_slash(bot, monkeypatch, "dev schedule", {"when": "in 2h", "title": "Maintenance", "body": "See you soon"})
    assert ctx.command_failed is False
    assert await bot.announcements.pending_count(1) == 1
    assert ctx.send.call_args.kwargs["ephemeral"] is True


async def test_regular_commands_are_hybrid_but_private_operations_remain_slash_only(command_bot):
    bot = command_bot
    assert len(bot.cogs) == len(COGS)
    for path in ("help", "about", "invite", "ping", "status", "errors", "settings prefix", "settings config", "github watch", "github list", "dev announce", "dev nudge", "dev cancel"):
        command = bot.get_command(path)
        assert isinstance(command, commands.HybridCommand)
        assert command.app_command is not None
    assert bot.get_command("dashboard") is None
    assert bot.tree.get_command("dashboard") is not None
    assert bot.get_command("appardas") is None
    assert bot.tree.get_command("appardas").get_command("backup") is not None
    settings = bot.tree.get_command("settings")
    assert {choice.value for choice in settings.get_command("config").parameters[0].choices} >= {"prefix", "timezone"}
    assert settings.get_command("cog").parameters[1].choices[1].value == "off"
    assert settings.get_command("audit").parameters[0].min_value == 1
    assert settings.get_command("audit").parameters[0].max_value == 25


async def test_help_reports_the_prefix_and_text_reply_visibility(command_bot):
    await command_bot.settings_service.update_setting(1, "prefix", "!", 1)
    ctx = await invoke_text(command_bot, "!help settings")
    embed = ctx.send.call_args.kwargs["embed"]
    field = next(field for field in embed.fields if field.name == "Text commands")
    assert "!" in field.value
    assert "visible in the channel" in field.value


async def test_text_prefix_is_in_owner_backup(command_bot):
    await command_bot.settings_service.update_setting(1, "prefix", "!", 1)
    export = await command_bot.get_cog("StatusCog")._build_export()
    assert json.loads(export["guild_settings"][0]["settings"])["prefix"] == "!"


async def test_text_input_errors_are_replied_to_without_recording_a_fault(command_bot):
    ctx = await context(command_bot, "<@99> errors 999")
    with pytest.raises(commands.UserInputError) as excinfo:
        await ctx.command.invoke(ctx)
    await command_bot.on_command_error(ctx, excinfo.value)
    assert "Usage:" in ctx.send.call_args.args[0]
    assert await command_bot.db.scalar("SELECT COUNT(*) FROM cog_errors") == 0


async def test_command_check_and_hybrid_errors_use_the_correct_reply(command_bot):
    ctx = await context(command_bot, "<@99> help")
    await command_bot.on_command_error(ctx, commands.CheckFailure("maintenance"))
    assert ctx.send.call_args.args[0] == "maintenance"
    await command_bot.on_command_error(ctx, commands.HybridCommandError(app_commands.CheckFailure("denied")))
    assert ctx.send.call_args.args[0] == "denied"
    await command_bot.on_command_error(ctx, commands.CommandInvokeError(RuntimeError("private details")))
    assert "private details" not in ctx.send.call_args.args[0]
    row = await command_bot.db.fetchone("SELECT cog, command, error_type FROM cog_errors")
    assert row == {"cog": "utility", "command": "help", "error_type": "RuntimeError"}


async def test_hybrid_callback_error_records_the_underlying_exception(command_bot, monkeypatch):
    bot = command_bot
    bot.status_service.snapshot.side_effect = RuntimeError("private details")
    interaction = slash_interaction(bot, monkeypatch, "status", {})
    await bot.tree._call(interaction)
    ctx = interaction._baton
    assert ctx.command_failed is True
    error_ctx, error = bot.dispatch.call_args.args[1:]
    assert isinstance(error, commands.HybridCommandError)
    assert isinstance(error.original, app_commands.CommandInvokeError)
    await bot.on_command_error(error_ctx, error)
    reply = interaction.response.send_message.call_args.kwargs["content"]
    assert "RuntimeError" in reply
    assert "private details" not in reply
    row = await bot.db.fetchone("SELECT cog, command, error_type FROM cog_errors")
    assert row == {"cog": "status", "command": "status", "error_type": "RuntimeError"}
    bot.status_service.mark_loop_error.assert_called_once_with("status: RuntimeError")
