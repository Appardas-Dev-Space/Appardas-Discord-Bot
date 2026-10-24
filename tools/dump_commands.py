#!/usr/bin/env python3
"""Generate app/commands.json from the live command tree.

    python tools/dump_commands.py            # write the manifest
    python tools/dump_commands.py --check    # fail if it is stale

No token or network access is required: cogs are registered on a bot that is
never logged in.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import discord  # noqa: E402
from discord.ext import commands  # noqa: E402

from app.cogs.registry import COGS  # noqa: E402
from app.commands_export import OUTPUT, build_manifest  # noqa: E402


async def _manifest() -> dict:
    intents = discord.Intents.none()
    intents.guilds = True
    bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)
    try:
        for meta in COGS.values():
            module = importlib.import_module(meta.module)
            await bot.add_cog(getattr(module, meta.class_name)(bot))
        return build_manifest(bot.tree)
    finally:
        await bot.close()


def _canonical(payload: dict) -> str:
    return json.dumps(payload, indent=2) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="compare app/commands.json with the live tree instead of writing",
    )
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    try:
        manifest = asyncio.run(_manifest())
    except Exception as exc:
        print(f"could not build the command manifest: {exc}", file=sys.stderr)
        return 1

    rendered = _canonical(manifest)
    if args.check:
        if not args.output.exists():
            print(f"{args.output} does not exist; run the dumper.", file=sys.stderr)
            return 1
        current = args.output.read_text(encoding="utf-8")
        if current != rendered:
            print(
                "app/commands.json is stale. A command was added, removed or renamed "
                "without updating app/commands_docs.py.\n"
                "Run `python tools/dump_commands.py` and commit the result.",
                file=sys.stderr,
            )
            return 1
        print("app/commands.json matches the live command tree.")
        return 0

    args.output.write_text(rendered, encoding="utf-8")
    total = sum(len(cog["commands"]) for cog in manifest["cogs"].values())
    print(f"wrote {args.output} · {len(manifest['cogs'])} cogs · {total} commands")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
