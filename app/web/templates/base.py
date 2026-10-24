"""Pure HTML shell for the account-gated console."""

from __future__ import annotations

import html
from typing import Any

from ...version import VERSION
from ..asset_store import CSS_NAME, FONT_NAME, JS_NAME, LOGO_NAME, TOKENS_NAME

NAV = (("overview", "Overview"), ("cogs", "Cogs"), ("settings", "Settings"), ("watches", "Watches"), ("audit", "Audit"))


def e(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def csrf_field(csrf: str) -> str:
    return f'<input type="hidden" name="csrf" value="{e(csrf)}">'


def layout(*, title: str, active: str, content: str, account: Any, csrf: str, guilds: list[dict], guild: Any = None, verdict: str = "", live: bool = False) -> str:
    base = f"/dashboard/g/{guild.id}" if guild else ""
    nav = "".join(f'<a href="{base}{"/" + key if key != "overview" else ""}"{" aria-current=page" if active == key else ""}>{label}</a>' for key, label in NAV) if guild else '<a href="/dashboard">Servers</a>'
    admin = '<p class="nav-group">Admin</p><nav aria-label="Administration">' + "".join(f'<a href="/dashboard/admin/{key}"{" aria-current=page" if active == key else ""}>{label}</a>' for key, label in (("accounts", "Accounts"), ("activity", "Activity"))) + '</nav>' if account.role == "owner" else ""
    logout = f'<form method="post" action="/logout">{csrf_field(csrf)}<button class="plain-button" type="submit">Sign out</button></form>'
    brand = f'<a class="brand" href="/"><img class="brand-logo" src="/assets/{LOGO_NAME}" alt="appardas" width="28" height="18"><span>appardas</span></a>'
    account_nav = f'<div class="account-nav"><p>{e(account.name)}</p><p class="muted">{e(account.role)}</p>{logout}</div>'
    links = "".join(f'<a href="/dashboard/g/{item["id"]}">{e(item["name"])}</a>' for item in guilds)
    scope = f'<details><summary>{e(guild.name if guild else "All servers")} ▾</summary><nav class="scope-menu" aria-label="Servers">{links or "No servers assigned"}</nav></details>'
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="color-scheme" content="dark"><meta name="robots" content="noindex, nofollow"><title>{e(title)} · appardas</title><link rel="icon" href="/favicon.svg" type="image/svg+xml"><link rel="preload" href="/assets/{FONT_NAME}" as="font" type="font/woff2" crossorigin><link rel="stylesheet" href="/assets/{TOKENS_NAME}"><link rel="stylesheet" href="/assets/{CSS_NAME}"><script defer src="/assets/{JS_NAME}"></script></head>
<body data-live-guild="{guild.id if guild and live else ''}"><a class="skip" href="#content">Skip to content</a><div class="app-shell"><aside class="sidebar">{brand}<nav aria-label="Server sections">{nav}</nav>{admin}{account_nav}</aside><div class="app-plane"><header class="topbar"><div class="mobile-nav"><details><summary aria-label="Open navigation">Menu</summary><div class="mobile-sheet">{brand}<nav aria-label="Server sections">{nav}</nav>{admin}{account_nav}</div></details></div><div class="scope">{scope}</div><span class="verdict {e(verdict)}">{e(verdict or 'Account console')}</span><a href="/commands">Commands</a></header><main class="app-main" id="content">{content}<p class="form-status" role="status" data-live-notice></p><footer class="app-footer"><span>appardas {VERSION} · one process, five cogs</span><a href="/">Public status</a></footer></main></div></div>{f'<nav class="bottom-nav" aria-label="Quick navigation">{nav}</nav>' if guild else ''}</body></html>'''
