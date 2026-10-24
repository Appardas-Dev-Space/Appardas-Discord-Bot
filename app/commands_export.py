"""Commands manifest export.

Used by `/appardas dump-commands` and by `tools/dump_commands.py`. The live
tree is the source of truth for names; `commands_docs.py` supplies the prose.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from discord import app_commands

from .commands_manifest import build_manifest as live_manifest, collect_paths, missing_docs, orphaned_docs
from .version import VERSION

OUTPUT = Path(__file__).with_name("commands.json")


def build_manifest(tree: app_commands.CommandTree[Any]) -> dict[str, Any]:
    """Deterministic, strict debug export for the CI drift fixture."""
    return live_manifest(tree, strict=True, version=VERSION)


def write_manifest(
    tree: app_commands.CommandTree[Any], output: Path | None = None
) -> Path:
    path = output or OUTPUT
    payload = build_manifest(tree)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path
