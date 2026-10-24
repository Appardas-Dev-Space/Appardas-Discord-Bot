"""Entrypoint: one process, one event loop, the bot and its HTTP port.

`python -m app.main`
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import logging
import signal
import sys
from typing import Any

import discord
from discord import app_commands
from discord.ext import commands

from . import VERSION
from .checks import CommandSource, global_check
from .cogs.registry import COGS, class_key
from .config import ConfigError, Settings, load_settings
from .commands_manifest import build_manifest
from .db import Database, DatabaseError
from .events import bus
from .logging_setup import setup_logging
from .prefixes import resolve_prefix
from .services.announcements import AnnouncementService
from .services.audit import AuditService
from .services.accounts import AccountsService
from .services.activity import ActivityService
from .services.commands import CommandLogService
from .services.github import GitHubService
from .services.nudge import NudgeService
from .services.settings import SettingsService
from .services.status import StatusService
from .util import iso
from .views.embeds import MUTED_COLOR
from .web.status import start_server
from .web.sessions import Sessions

log = logging.getLogger(__name__)


class AppBot(commands.Bot):
    def __init__(self, config: Settings) -> None:
        intents = discord.Intents.none()
        intents.guilds = True
        intents.members = config.enable_member_intent
        intents.guild_messages = True
        intents.message_content = config.enable_message_content_intent
        super().__init__(
            command_prefix=resolve_prefix,
            intents=intents,
            help_command=None,
            strip_after_prefix=True,
            allowed_mentions=discord.AllowedMentions.none(),
            activity=None,
        )
        self.add_check(global_check)
        self.cfg = config
        self.db: Database | None = None
        self.settings_service: SettingsService | None = None
        self.audit: AuditService | None = None
        self.command_log: CommandLogService | None = None
        self.announcements: AnnouncementService | None = None
        self.nudges: NudgeService | None = None
        self.github: GitHubService | None = None
        self.status_service: StatusService | None = None
        self.accounts: AccountsService | None = None
        self.activity_service: ActivityService | None = None
        self.sessions: Sessions | None = None
        self.manifest: dict[str, Any] = {}
        self.boot_stage = "database"
        self.event_bus = bus
        self.cog_faults: dict[str, str] = {}
        self.shutdown_requested = False
        self._prepared = False
        self._scheduler_tasks: list[asyncio.Task[Any]] = []
        self._modlog_queue: asyncio.Queue[dict[str, Any]] | None = None

    # ------------------------------------------------------------- lifecycle
    async def prepare(self) -> None:
        if self._prepared:
            return
        cfg = self.cfg
        self.db = Database(cfg.database_path)
        await self.db.connect(owner_ids=cfg.owner_ids)
        self.accounts = AccountsService(self.db)
        self.activity_service = ActivityService(self.db, trust_proxy=cfg.trust_proxy)
        self.sessions = Sessions(self.db, cfg)
        self.audit = AuditService(self.db)
        self.command_log = CommandLogService(self.db)
        self.settings_service = SettingsService(
            self.db, cfg, self.audit, faults=self.cog_faults, event_bus=self.event_bus
        )
        self.announcements = AnnouncementService(self.db, cfg, self.audit)
        self.nudges = NudgeService(self.db, cfg, self.audit)
        self.status_service = StatusService(self.db, cfg, self.settings_service)
        self.github = GitHubService(self.db, cfg, self.audit, self.status_service)
        await self.github.start()
        self.status_service.bind(self)

        self.tree.interaction_check = global_check
        self.tree.on_error = self._on_tree_error  # type: ignore[assignment]

        self.boot_stage = "cogs"
        await self._load_cogs()
        self.refresh_manifest()
        self.boot_stage = "http"
        await self.status_service.start_monitors()
        with contextlib.suppress(Exception):
            await self.status_service.refresh()
        self._prepared = True
        log.info(
            "prepared: db=%s public_origin=%s cogs=%s faults=%s",
            cfg.database_path,
            cfg.public_base_url,
            ",".join(COGS),
            ",".join(self.cog_faults) or "none",
        )

    def refresh_manifest(self) -> None:
        self.manifest = build_manifest(self.tree)

    async def _load_cogs(self) -> None:
        for key, meta in COGS.items():
            try:
                module = importlib.import_module(meta.module)
                cls = getattr(module, meta.class_name)
                await self.add_cog(cls(self))
                self.cog_faults.pop(key, None)
            except Exception as exc:
                self.cog_faults[key] = type(exc).__name__
                log.exception("cog %s failed to load; the other cogs keep running", key)
                if self.db is not None:
                    with contextlib.suppress(Exception):
                        await self.db.execute(
                            "INSERT INTO cog_errors (guild_id, cog, command, error_type, occurred_at) "
                            "VALUES (NULL, ?, NULL, ?, ?)",
                            (key, type(exc).__name__, iso()),
                        )
                if self.settings_service is not None:
                    self.settings_service.set_fault(key, type(exc).__name__)

    async def reload_cog(self, key: str) -> None:
        if key not in COGS:
            raise ValueError(f"unknown cog {key}")
        meta = COGS[key]
        if self.get_cog(meta.class_name) is not None:
            await self.remove_cog(meta.class_name)
        try:
            module = importlib.import_module(meta.module)
            module = importlib.reload(module)
            cls = getattr(module, meta.class_name)
            await self.add_cog(cls(self))
            self.cog_faults.pop(key, None)
            if self.settings_service is not None:
                self.settings_service.clear_fault(key)
        except Exception as exc:
            self.cog_faults[key] = type(exc).__name__
            if self.settings_service is not None:
                self.settings_service.set_fault(key, type(exc).__name__)
            raise
        finally:
            self.refresh_manifest()

    async def update_presence(
        self,
        text: str | None = None,
        type: str | None = None,
        status: str | None = None,
        *,
        conn: Any = None,
    ) -> dict[str, Any]:
        if self.status_service is None:
            raise RuntimeError("status service not ready")
        info = await self.status_service.set_presence(text=text, type=type, status=status, conn=conn)
        activity = self.status_service.build_activity(info["text"], info["type"])
        discord_status = self.status_service.resolve_status(info["status"])
        if getattr(self, "ws", None) is not None:
            with contextlib.suppress(Exception):
                await self.change_presence(activity=activity, status=discord_status)
        return info

    async def setup_hook(self) -> None:
        await self.prepare()
        if self.cfg.command_sync_on_start:
            try:
                synced, scope = await self.sync_tree()
                log.info("synced %s commands (%s)", synced, scope)
            except Exception:
                log.exception("command sync failed; commands may be stale until /appardas sync")

    async def sync_tree(self) -> tuple[int, str]:
        """Sync the tree, leaving disabled cogs out of the picker.

        The command gate is authoritative; this is the cosmetic second layer
        from ARCHITECTURE §3.1. With a dev guild we can be exact. For a global
        sync the state is per guild, so a cog is only omitted when every known
        guild has it disabled or faulted.
        """
        if self.cfg.dev_guild_id:
            target: discord.Object | None = discord.Object(id=self.cfg.dev_guild_id)
            scope_guild: int | None = self.cfg.dev_guild_id
            scope = f"guild {self.cfg.dev_guild_id}"
        else:
            target = None
            scope_guild = None
            scope = "global"
        hidden = await self._hidden_cogs(scope_guild)
        original = list(self.tree.get_commands())
        self._detach_commands(hidden)
        try:
            synced = await self.tree.sync(guild=target)
        finally:
            if hidden:
                # Restore the exact pre-sync order, not just the removed commands.
                for command in list(self.tree.get_commands()):
                    self.tree.remove_command(command.name)
                for command in original:
                    self.tree.add_command(command)
            self.refresh_manifest()
        if hidden:
            log.info("tree sync excluded disabled cogs: %s", ",".join(sorted(hidden)))
        return len(synced), scope

    async def _hidden_cogs(self, guild_id: int | None) -> set[str]:
        if self.settings_service is None:
            return set(self.cog_faults)
        hidden = set(self.cog_faults)
        if guild_id is not None:
            for key in COGS:
                state = await self.settings_service.cog_state(guild_id, key)
                if not state.enabled or state.state == "faulted":
                    hidden.add(key)
            return hidden
        guilds = list(self.guilds)
        if not guilds and self.db is not None:
            rows = await self.db.fetchall(
                "SELECT id FROM guilds WHERE left_at IS NULL ORDER BY id"
            )
            guild_ids = [int(row["id"]) for row in rows]
        else:
            guild_ids = [guild.id for guild in guilds]
        if not guild_ids:
            return hidden
        for key in COGS:
            if key in hidden:
                continue
            states = [
                await self.settings_service.cog_state(guild_id, key)
                for guild_id in guild_ids
            ]
            if all(not state.enabled or state.state == "faulted" for state in states):
                hidden.add(key)
        return hidden

    def _detach_commands(self, hidden: set[str]) -> list[Any]:
        if not hidden:
            return []
        removed: list[Any] = []
        for command in list(self.tree.get_commands()):
            if self._command_cog_key(command) in hidden:
                self.tree.remove_command(command.name)
                removed.append(command)
        return removed

    def _command_cog_key(self, command: Any) -> str | None:
        # Groups are bound to the cog only through their children in discord.py.
        key = class_key(getattr(command, "binding", None))
        if key is not None:
            return key
        if isinstance(command, app_commands.Group):
            for child in command.commands:
                key = self._command_cog_key(child)
                if key is not None:
                    return key
        return None

    async def on_ready(self) -> None:
        self.boot_stage = "ready"
        assert self.user is not None
        log.info("logged in as %s (%s) · %s guilds", self.user, self.user.id, len(self.guilds))
        if self.settings_service is not None:
            for guild in self.guilds:
                with contextlib.suppress(Exception):
                    await self.settings_service.ensure_guild(guild.id, guild.name)
            with contextlib.suppress(Exception):
                await self.settings_service.seed_defaults()
        if self.status_service is not None:
            presence_info = await self.status_service.get_presence()
            activity = self.status_service.build_activity(
                presence_info["text"], presence_info["type"]
            )
            discord_status = self.status_service.resolve_status(presence_info["status"])
            with contextlib.suppress(Exception):
                await self.change_presence(activity=activity, status=discord_status)
        else:
            activity_type = {
                "playing": discord.ActivityType.playing,
                "watching": discord.ActivityType.watching,
                "listening": discord.ActivityType.listening,
                "competing": discord.ActivityType.competing,
            }.get(self.cfg.activity_type, discord.ActivityType.watching)
            with contextlib.suppress(Exception):
                await self.change_presence(
                    activity=discord.Activity(type=activity_type, name=self.cfg.activity_text)
                )
        if self.status_service is not None:
            await self.status_service.record_heartbeat(True)
            await self.status_service.refresh()
        self._start_scheduler()

    async def on_guild_join(self, guild: discord.Guild) -> None:
        if self.settings_service is not None:
            await self.settings_service.ensure_guild(guild.id, guild.name)
        if self.audit is not None:
            await self.audit.record(
                guild_id=guild.id,
                actor_id=None,
                action="guild.join",
                target=guild.name,
                detail={"source": "system"},
            )

    async def on_guild_remove(self, guild: discord.Guild) -> None:
        if self.settings_service is not None:
            await self.settings_service.forget_guild(guild.id)
        if self.audit is not None:
            await self.audit.record(
                guild_id=guild.id,
                actor_id=None,
                action="guild.left",
                target=guild.name,
                detail={"source": "system"},
            )

    # ----------------------------------------------------------- command hooks
    def _extract_interaction_command(
        self, data: dict[str, Any] | None
    ) -> tuple[str, dict[str, Any]]:
        if not data:
            return "unknown", {}
        parts = [data.get("name", "unknown")]
        options = data.get("options", [])
        while options and len(options) == 1 and options[0].get("type") in (1, 2):
            parts.append(options[0].get("name", ""))
            options = options[0].get("options", [])
        args = {}
        for opt in options:
            if "name" in opt:
                args[opt["name"]] = opt.get("value")
        return " ".join(parts), args

    async def on_interaction(self, interaction: discord.Interaction) -> None:
        if (
            interaction.type == discord.InteractionType.application_command
            and self.command_log is not None
        ):
            cmd_name, opts = self._extract_interaction_command(interaction.data)
            user_name = (
                f"{interaction.user.name}#{interaction.user.discriminator}"
                if getattr(interaction.user, "discriminator", "0") not in ("0", None, "")
                else interaction.user.name
            )
            with contextlib.suppress(Exception):
                log_id = await self.command_log.record(
                    guild_id=interaction.guild_id,
                    channel_id=interaction.channel_id,
                    user_id=interaction.user.id,
                    user_name=user_name,
                    command=cmd_name,
                    options=opts,
                    status="success",
                )
                interaction.extras["command_log_id"] = log_id
        await super().on_interaction(interaction)

    async def on_command(self, ctx: commands.Context) -> None:
        if self.command_log is not None:
            user_name = (
                f"{ctx.author.name}#{ctx.author.discriminator}"
                if getattr(ctx.author, "discriminator", "0") not in ("0", None, "")
                else ctx.author.name
            )
            cmd_name = ctx.command.qualified_name if ctx.command else (ctx.invoked_with or "unknown")
            opts = {
                "args": [str(a) for a in ctx.args[2:]],
                "kwargs": {k: str(v) for k, v in ctx.kwargs.items()},
            }
            with contextlib.suppress(Exception):
                log_id = await self.command_log.record(
                    guild_id=ctx.guild.id if ctx.guild else None,
                    channel_id=ctx.channel.id if ctx.channel else None,
                    user_id=ctx.author.id,
                    user_name=user_name,
                    command=cmd_name,
                    options=opts,
                    status="success",
                )
                setattr(ctx, "command_log_id", log_id)

    # ----------------------------------------------------------------- loops
    def _start_scheduler(self) -> None:
        if self._scheduler_tasks:
            return
        if self._modlog_queue is None:
            self._modlog_queue = self.event_bus.subscribe()
        self._scheduler_tasks = [
            asyncio.create_task(self._dispatch_loop(), name="dispatch-announcements"),
            asyncio.create_task(self._github_loop(), name="poll-github"),
            asyncio.create_task(self._prune_loop(), name="prune"),
            asyncio.create_task(self._heartbeat_loop(), name="heartbeat-status"),
            asyncio.create_task(self._modlog_loop(), name="mod-log"),
        ]

    async def _cog_enabled(self, guild_id: int, cog: str) -> bool:
        if self.settings_service is None:
            return False
        if cog in self.cog_faults:
            return False
        return await self.settings_service.is_enabled(guild_id, cog)

    async def _dispatch_loop(self) -> None:
        await self.wait_until_ready()
        await asyncio.sleep(5)
        while True:
            try:
                if self.announcements is not None:
                    result = await self.announcements.dispatch(self, self._cog_enabled)
                    if self.status_service is not None:
                        self.status_service.mark_scheduler("announce_dispatch")
                    if result.sent or result.failed:
                        log.info(
                            "announcements: sent=%s failed=%s skipped=%s",
                            result.sent,
                            result.failed,
                            result.skipped,
                        )
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("announcement dispatcher failed")
                if self.status_service is not None:
                    self.status_service.mark_loop_error("announcement dispatcher failed")
            await asyncio.sleep(30)

    async def _github_loop(self) -> None:
        await self.wait_until_ready()
        await asyncio.sleep(10)
        while True:
            try:
                if self.github is not None:
                    result = await self.github.poll_all(self, self._cog_enabled)
                    if self.status_service is not None:
                        self.status_service.mark_scheduler("github_poll")
                        self.status_service.github_rate_remaining = self.github.rate_limit_remaining
                    if result["posted"] or result["failed"]:
                        log.info("github poll: %s", result)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("GitHub poller failed")
                if self.status_service is not None:
                    self.status_service.mark_loop_error("GitHub poller failed")
            await asyncio.sleep(self.cfg.github_poll_interval_s)

    async def _prune_loop(self) -> None:
        await self.wait_until_ready()
        await asyncio.sleep(3600)
        while True:
            try:
                if self.status_service is not None:
                    counts = await self.status_service.prune()
                    log.info("prune: %s", counts)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("prune failed")
            await asyncio.sleep(86400)

    async def _heartbeat_loop(self) -> None:
        """Record one 5-minute heartbeat bucket every minute (ARCHITECTURE §5)."""
        await self.wait_until_ready()
        while True:
            try:
                if self.status_service is not None:
                    await self.status_service.record_heartbeat(self.is_ready())
                    self.status_service.mark_scheduler("heartbeat_status")
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("heartbeat loop failed")
            await asyncio.sleep(60)

    async def _modlog_loop(self) -> None:
        """Mirror toggle/config events to the guild's `mod_log` channel, if set."""
        await self.wait_until_ready()
        queue = self._modlog_queue
        if queue is None:
            return
        while True:
            try:
                event = await queue.get()
                await self._post_modlog(event)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("mod log loop failed")

    async def _post_modlog(self, event: dict[str, Any]) -> None:
        if self.settings_service is None:
            return
        guild_id = event.get("guild_id")
        if not isinstance(guild_id, int):
            return
        kind = event.get("type")
        if kind not in ("cog.toggle", "config.set"):
            return
        channel_id = await self.settings_service.get_setting(guild_id, "mod_log")
        if not channel_id:
            return
        guild = self.get_guild(guild_id)
        if guild is None:
            return
        channel = guild.get_channel(int(channel_id))
        if not isinstance(channel, discord.TextChannel):
            return
        if kind == "cog.toggle":
            state = "on" if event.get("enabled") else "off"
            embed = discord.Embed(
                title="Cog toggle",
                description=f"`{event.get('cog')}` is now **{state}**",
                color=MUTED_COLOR,
            )
            reason = event.get("reason")
            if reason:
                embed.add_field(name="Reason", value=str(reason)[:500], inline=False)
        else:
            embed = discord.Embed(
                title="Config change",
                description=f"`{event.get('key')}` → `{event.get('value')}`",
                color=MUTED_COLOR,
            )
        actor_id = event.get("actor_id")
        embed.add_field(
            name="Actor", value=f"<@{actor_id}>" if actor_id else "system", inline=True
        )
        embed.add_field(name="Source", value=str(event.get("source", "bot")), inline=True)
        embed.set_footer(text="appardas · mod log")
        try:
            await channel.send(
                embed=embed, allowed_mentions=discord.AllowedMentions.none()
            )
        except Exception as exc:
            log.warning(
                "mod log post failed guild=%s channel=%s: %s", guild_id, channel_id, exc
            )

    # ----------------------------------------------------------------- errors
    async def on_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
        # Slash-only commands (and ordinary chat) must not produce noisy errors.
        if isinstance(error, commands.CommandNotFound):
            return
        original: Exception = getattr(error, "original", error)
        # Hybrid callbacks can wrap an app-command invocation error as well.
        while isinstance(original, (commands.HybridCommandError, commands.CommandInvokeError, app_commands.CommandInvokeError)):
            original = original.original
        if self.command_log is not None and hasattr(ctx, "command_log_id"):
            log_id = getattr(ctx, "command_log_id")
            if log_id:
                status = "denied" if isinstance(original, (commands.CheckFailure, app_commands.CheckFailure)) else "error"
                with contextlib.suppress(Exception):
                    await self.command_log.set_status(log_id, status)

        if isinstance(original, (commands.CommandOnCooldown, app_commands.CommandOnCooldown)):
            await self._reply_error(ctx, f"Slow down — retry in {original.retry_after:.1f}s.")
            return
        if isinstance(original, (commands.CheckFailure, app_commands.CheckFailure)):
            await self._reply_error(ctx, str(original) or "You cannot use this command.")
            return
        if isinstance(original, (commands.UserInputError, app_commands.TransformerError)):
            usage = f"{ctx.clean_prefix}{ctx.command.qualified_name} {ctx.command.signature}" if ctx.command else "/help"
            await self._reply_error(ctx, f"Invalid arguments. Usage: {discord.utils.escape_markdown(usage)}")
            return

        command = ctx.command.qualified_name if ctx.command else "unknown"
        cog = class_key(getattr(ctx.command, "cog", None)) or "unknown"
        error_type = type(original).__name__
        log.exception("command error: cog=%s command=%s", cog, command, exc_info=original)
        if self.db is not None:
            with contextlib.suppress(Exception):
                await self.db.execute(
                    "INSERT INTO cog_errors (guild_id, cog, command, error_type, occurred_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (ctx.guild.id if ctx.guild else None, cog, command, error_type, iso()),
                )
        if self.status_service is not None:
            self.status_service.mark_loop_error(f"{command}: {error_type}")
        await self._reply_error(
            ctx,
            f"Something went wrong (`{error_type}`). An operator can run `/errors` for the recorded exception type.",
        )

    async def _on_tree_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        original = error.original if isinstance(error, app_commands.CommandInvokeError) else error
        if self.command_log is not None:
            log_id = interaction.extras.get("command_log_id")
            if log_id:
                status = "denied" if isinstance(original, (app_commands.CheckFailure, app_commands.MissingPermissions)) else "error"
                with contextlib.suppress(Exception):
                    await self.command_log.set_status(log_id, status)
        if isinstance(original, app_commands.CheckFailure):
            await self._reply_error(interaction, str(original) or "You cannot use this command.")
            return
        if isinstance(original, app_commands.CommandOnCooldown):
            await self._reply_error(
                interaction, f"Slow down — retry in {original.retry_after:.1f}s."
            )
            return
        if isinstance(original, app_commands.MissingPermissions):
            await self._reply_error(
                interaction,
                "You need **Manage Server** for this command.",
            )
            return

        command = interaction.command.qualified_name if interaction.command else "unknown"
        cog = class_key(getattr(interaction.command, "binding", None)) or "unknown"
        error_type = type(original).__name__
        log.exception(
            "command error: cog=%s command=%s", cog, command, exc_info=original
        )
        if self.db is not None:
            with contextlib.suppress(Exception):
                await self.db.execute(
                    "INSERT INTO cog_errors (guild_id, cog, command, error_type, occurred_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (interaction.guild_id, cog, command, error_type, iso()),
                )
        if self.status_service is not None:
            self.status_service.mark_loop_error(f"{command}: {error_type}")
        await self._reply_error(
            interaction,
            f"Something went wrong (`{error_type}`). "
            "An operator can run `/errors` for the recorded exception type.",
        )

    async def _reply_error(self, interaction: CommandSource, message: str) -> None:
        try:
            if isinstance(interaction, commands.Context):
                await interaction.send(message, ephemeral=True)
            elif interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except Exception:
            log.debug("could not deliver an error reply", exc_info=True)

    # -------------------------------------------------------------- shutdown
    async def shutdown(self) -> None:
        for task in self._scheduler_tasks:
            task.cancel()
        if self._scheduler_tasks:
            await asyncio.gather(*self._scheduler_tasks, return_exceptions=True)
            self._scheduler_tasks.clear()
        if self._modlog_queue is not None:
            self.event_bus.unsubscribe(self._modlog_queue)
            self._modlog_queue = None
        if self.status_service is not None:
            await self.status_service.stop_monitors()
        if self.github is not None:
            await self.github.close()
        if self.db is not None:
            await self.db.close()
        log.info("shutdown complete")


async def run() -> int:
    try:
        cfg = load_settings()
    except ConfigError as exc:
        print(f"appardas cannot start.\n{exc}", file=sys.stderr)
        return 2
    setup_logging(cfg.log_dir, cfg.log_level, cfg.log_max_bytes, cfg.log_backups)
    log.info("appardas %s starting (environment=%s)", VERSION, cfg.environment)
    if cfg.http_host not in ("0.0.0.0", "::"):
        log.warning(
            "HTTP_HOST=%s — /health will not be reachable from outside the container. "
            "Use 0.0.0.0 in production.",
            cfg.http_host,
        )
    if not cfg.github_token:
        log.warning(
            "GITHUB_TOKEN is unset — GitHub allows 60 requests/hour anonymously. "
            "The 5-watch cap and ETags keep you under it, but a token is strongly advised."
        )

    bot = AppBot(cfg)

    loop = asyncio.get_running_loop()

    def _request_shutdown(signame: str) -> None:
        log.warning("received %s — closing the bot cleanly", signame)
        bot.shutdown_requested = True
        asyncio.create_task(bot.close())

    for signame, sig in (("SIGTERM", signal.SIGTERM), ("SIGINT", signal.SIGINT)):
        with contextlib.suppress(NotImplementedError, RuntimeError, AttributeError):
            loop.add_signal_handler(sig, _request_shutdown, signame)

    runner = None
    try:
        await bot.prepare()
        runner, _err = await start_server(bot)
        bot.boot_stage = "discord"
        await bot.start(cfg.discord_token)
    except DatabaseError as exc:
        log.error("database startup failed: %s", exc)
        return 3
    except discord.LoginFailure:
        log.error("Discord rejected the token. Paste the raw bot token, without the 'Bot ' prefix.")
        return 4
    except asyncio.CancelledError:
        pass
    finally:
        if runner is not None:
            with contextlib.suppress(Exception):
                await runner.cleanup()
        with contextlib.suppress(Exception):
            await bot.shutdown()
    return 0


def main() -> None:
    try:
        exit_code = asyncio.run(run())
    except KeyboardInterrupt:
        exit_code = 0
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
