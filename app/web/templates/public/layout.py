"""Shared public landmarks, metadata and immutable assets."""

from __future__ import annotations

from ....version import VERSION
from ...asset_store import APPA_DEV_LOGO_NAME, FONT_NAME, LOGO_NAME, PUBLIC_CSS_NAME, PUBLIC_JS_NAME, TOKENS_NAME
from ..base import e


def layout(*, title: str, description: str, content: str, base_url: str, path: str = "/", invite: str | None = None, private: bool = False, close: bool = False, account: object | None = None) -> str:
    meta = '<meta name="robots" content="noindex, nofollow">' if private else f'<link rel="canonical" href="{e(base_url + path)}">'
    close_title = '<h2>Dedicated operations.<br>One process.</h2>' if close else '<h2>Five cogs.<br>One process.</h2>'
    sign_in_link = '<a href="/dashboard">Dashboard</a>' if account else '<a href="/login">Sign in</a>'
    action = ('<a class="primary" href="/dashboard">Open Console</a>' if account else '<a class="primary" href="/login">Console Sign In</a>') if close else ''
    brand = f'<a class="wordmark" href="/" title="appardas | appa-dev bot"><img class="brand-logo" src="/assets/{LOGO_NAME}" alt="appardas" width="32" height="20"><span>appardas</span><span class="brand-sep" aria-hidden="true">|</span><img class="brand-logo bot-logo" src="/assets/{APPA_DEV_LOGO_NAME}" alt="appa-dev bot" width="26" height="26"><span class="brand-sub">appa-dev bot</span></a>'
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><meta name="color-scheme" content="dark"><title>{e(title)} · appardas</title><meta name="description" content="{e(description)}">{meta}<link rel="icon" href="/favicon.svg" type="image/svg+xml"><link rel="preload" href="/assets/{FONT_NAME}" as="font" type="font/woff2" crossorigin><link rel="stylesheet" href="/assets/{TOKENS_NAME}"><link rel="stylesheet" href="/assets/{PUBLIC_CSS_NAME}"><script defer src="/assets/{PUBLIC_JS_NAME}"></script></head><body><a class="skip" href="#content">Skip to content</a><header class="public-nav">{brand}<nav class="nav-links" aria-label="Main"><a class="wide-link" href="/commands">Commands</a><a class="wide-link" href="/#right-now">Status</a>{sign_in_link}</nav></header><main class="public-main" id="content">{content}</main><footer class="public-close"><div class="close-inner"><div class="close-top">{close_title}<nav aria-label="Footer"><a href="/commands">Commands</a><a href="/getting-started">Guide</a><a href="/#right-now">Live status</a>{sign_in_link}</nav></div>{action}<div class="close-meta"><p>appardas {VERSION}</p><p>This page runs with the bot. When the process stops, so does the site.</p></div><p class="close-wordmark" aria-hidden="true">appardas</p></div></footer></body></html>'''
