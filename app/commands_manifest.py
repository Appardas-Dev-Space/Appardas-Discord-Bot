"""The public documentation comes from the loaded tree, never a file copy."""

from __future__ import annotations

import logging
import time
from typing import Any

from discord import app_commands

from .cogs.registry import COGS, class_key
from .commands_docs import COG_SUMMARIES, COMMANDS
from .version import VERSION

log = logging.getLogger(__name__)


def _leaves(commands: list[Any]) -> list[Any]:
    result = []
    for command in commands:
        if isinstance(command, app_commands.Group):
            result.extend(_leaves(list(command.commands)))
        else:
            result.append(command)
    return result


def collect_paths(tree: app_commands.CommandTree[Any]) -> tuple[list[str], list[str]]:
    groups: list[str] = []

    def walk(commands: list[Any]) -> None:
        for command in commands:
            if isinstance(command, app_commands.Group):
                groups.append(command.qualified_name)
                walk(list(command.commands))

    walk(list(tree.get_commands()))
    return [command.qualified_name for command in _leaves(list(tree.get_commands()))], groups


def missing_docs(leaves: list[str]) -> list[str]:
    return [path for path in leaves if path not in COMMANDS]


def orphaned_docs(leaves: list[str]) -> list[str]:
    return [path for path in COMMANDS if path not in set(leaves)]


def build_manifest(tree: app_commands.CommandTree[Any], *, strict: bool = False, version: str | None = None) -> dict[str, Any]:
    commands = _leaves(list(tree.get_commands()))
    paths = [command.qualified_name for command in commands]
    missing, orphaned = missing_docs(paths), orphaned_docs(paths)
    problems = []
    if missing:
        problems.append("commands without prose: " + ", ".join(sorted(missing)))
    if orphaned:
        problems.append("prose without a loaded command: " + ", ".join(sorted(orphaned)))
    if problems:
        if strict:
            raise ValueError("; ".join(problems))
        log.warning("command documentation: %s", "; ".join(problems))
    cogs = {
        key: {"summary": COG_SUMMARIES[key]["summary"], "toggleable": not meta.locked, "locked": meta.locked, "commands": []}
        for key, meta in COGS.items()
    }
    for command in commands:
        path = command.qualified_name
        doc = COMMANDS.get(path)
        if doc is None:
            key = class_key(getattr(command, "binding", None)) or "utility"
            doc = {"cog": key, "usage": "/" + path, "description": getattr(command, "description", "Documentation is being updated."), "permissions": "see Discord", "examples": [], "failures": "Detailed notes are being updated."}
        cogs[str(doc["cog"] )]["commands"].append({"path": path, **doc})
    return {"version": version or str(time.time_ns()), "bot_version": VERSION, "generated_from": "live command tree", "cogs": cogs}
