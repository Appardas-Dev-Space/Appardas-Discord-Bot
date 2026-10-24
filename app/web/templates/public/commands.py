"""The live manifest is the only source for command rows and search."""

from __future__ import annotations

import re
from typing import Any

from ..base import e


def slug(path: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", path.lower()).strip("-")


def matches(command: dict[str, Any], query: str) -> bool:
    return query.casefold() in " ".join(str(command.get(key, "")) for key in ("path", "description", "usage")).casefold()


def cog_nav(manifest: dict[str, Any]) -> str:
    return '<nav class="cog-nav" aria-label="Command cogs">' + ''.join(f'<a href="/commands/{e(key)}">{e(key)}</a>' for key in manifest["cogs"]) + '</nav>'


def search_form(query: str = "", *, preview: bool = False) -> str:
    return f'<form class="command-search" method="get" action="/commands" data-command-search><label for="command-query">Search by command, purpose or usage</label><div class="search-line"><input id="command-query" name="q" type="search" value="{e(query)}" placeholder="Try github watch" maxlength="200" autocomplete="off"><button type="submit">Search</button></div></form><p class="search-count" role="status" data-search-count></p>'


def command_entry(command: dict[str, Any], cog: str, query: str = "") -> str:
    usage = command.get("usage", "/" + command["path"])
    examples = ''.join(f'<div class="example-row"><code>{e(example)}</code><button type="button" class="copy-button" data-copy="{e(example)}" aria-label="Copy {e(example)}">Copy</button></div>' for example in command.get("examples", []))
    details = f'<dt>Example</dt><dd class="examples">{examples}</dd>' if examples else ''
    if command.get("failures"):
        details += f'<dt>Note</dt><dd>{e(command["failures"])}</dd>'
    return f'<article class="command-entry" id="{e(slug(command["path"]))}" data-search{" hidden" if not matches(command, query) else ""}><div class="command-heading"><h3><a href="/commands/{e(cog)}#{e(slug(command["path"]))}">{e(usage)}</a></h3><span class="permission">{e(str(command.get("permissions", "everyone")).lower())}</span></div><p data-search-description>{e(command.get("description", ""))}</p><dl class="command-details">{details}</dl></article>'


def commands_page(manifest: dict[str, Any], *, cog: str | None = None, query: str = "") -> str:
    cogs = {cog: manifest["cogs"][cog]} if cog else manifest["cogs"]
    title = f"{cog} commands" if cog else "Every command."
    lead = cogs[cog]["summary"] if cog else "The commands loaded in this process, with their permissions, examples and failure notes."
    groups, count = [], 0
    for key, data in cogs.items():
        found = sum(matches(command, query) for command in data["commands"])
        count += found
        entries = ''.join(command_entry(command, key, query) for command in data["commands"])
        lock = "Always available: this cog keeps the bot recoverable." if data["locked"] else "May be paused per server. Pausing keeps its saved work."
        groups.append(f'<section class="docs-group" data-command-group{" hidden" if not found else ""}><h2>{e(key)}</h2><p>{e(data["summary"])} {lock}</p>{entries}</section>')
    empty = f'<p data-search-empty{" hidden" if count else ""}>No command matches “{e(query)}”. Try status, settings, github, dev or utility.</p>'
    return f'<header class="docs-header"><h1>{e(title)}</h1><p>{e(lead)}</p>{cog_nav(manifest)}</header><div class="docs-body" data-command-scope>{search_form(query)}<noscript><p>{count} commands match. Submit the search to filter.</p></noscript>{empty}{"".join(groups)}<p class="inline-copy-status" role="status" data-copy-status></p></div>'


def command_finder(manifest: dict[str, Any]) -> str:
    # All leaves stay in the DOM. Five suggestions make the unfiltered home short.
    commands = [(key, command) for key, cog in manifest["cogs"].items() for command in cog["commands"]]
    featured = {"status", "settings prefix", "github watch", "dev announce", "help"}
    commands.sort(key=lambda item: item[1]["path"] not in featured)
    rows = []
    for index, (key, command) in enumerate(commands):
        examples = command.get("examples", [])
        example = examples[0] if examples else command.get("usage", "/" + command["path"])
        rows.append(f'<article class="command-preview" data-search{" hidden" if index >= 5 else ""}><div><a href="/commands/{e(key)}#{e(slug(command["path"]))}">{e(command.get("usage", "/" + command["path"]))}</a><p data-search-description>{e(command.get("description", ""))}</p><p class="example">Example: {e(example)}</p></div></article>')
    return f'<section class="public-section" data-command-scope data-preview><h2 data-reveal>Command index.</h2>{search_form(preview=True)}<div class="search-examples"><button type="button" data-query="status">status</button><button type="button" data-query="github watch">github watch</button><button type="button" data-query="prefix">prefix</button></div><div class="preview-results">{"".join(rows)}</div><p data-search-empty hidden>No command matches.</p><div class="search-tail"><a href="/commands">View all {len(commands)} commands</a><p>Synchronized directly with the live command registry.</p></div></section>'
