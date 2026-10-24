"""Small, safe errors that keep the visitor oriented."""

from __future__ import annotations

from ..base import e


def error_page(status: int, message: str = "") -> str:
    title = "Page not found." if status == 404 else "Please try again." if status < 500 else "Something went wrong."
    message = message or ("That page is not here. The five cogs are status, settings, github, dev and utility." if status == 404 else "The bot log has the rest.")
    links = '<a href="/">Home</a><a href="/commands">Commands</a>'
    if status == 404:
        links += ''.join(f'<a href="/commands/{cog}">{cog}</a>' for cog in ("status", "settings", "github", "dev", "utility"))
    if status >= 500:
        links += '<a href="/health">Health</a>'
    return f'<div class="error-main"><h1>{title}</h1><p>{e(message)}</p><nav aria-label="Continue">{links}</nav></div>'
