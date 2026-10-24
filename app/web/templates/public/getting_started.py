"""Operational deployment guide: channel config, watches, and console setup."""

from __future__ import annotations

from ..base import e


def stages(invite: str | None = None) -> str:
    return '<div class="start-grid"><article data-reveal><h3>1. Deployment.</h3><p>Appardas is an exclusive operational bot configured for designated servers. Bot invitation is managed internally by administrators.</p><p class="start-note">Commands require Manage Server or bot operator roles.</p></article><article data-reveal><h3>2. Configure.</h3><p>Pick the channel for updates. Manage Server is required for these Discord commands.</p><code class="command-line">/settings config github_channel #releases</code><code class="command-line">/settings prefix !</code></article><article data-reveal><h3>3. First Watch.</h3><p>Watch a repository you care about, then check the connection.</p><code class="command-line">/github watch appardas/bot #releases</code><code class="command-line">/status</code></article></div>'


def getting_started_page(invite: str | None = None) -> str:
    return '<header class="docs-header"><h1>Operator Guide.</h1><p>Start with a channel and one watch. Settings and work stay in SQLite across restarts.</p></header><section class="docs-body"><h2>Three things to do.</h2>' + stages(invite) + '</section><section class="public-section"><h2>Operational Policies.</h2><div class="questions"><article class="question"><h3>Only five watches.</h3><p>A server can watch up to five GitHub targets. Updates are grouped and delivered to the channel you chose. Read <a href="/commands/github">GitHub commands</a> for event choices and failure notes.</p></article><article class="question"><h3>Prefix or mention.</h3><p>The prefix is optional. Leave it empty for mention-only commands, or use <code>/settings prefix !</code>. Prefix commands need the Message Content intent; slash commands always work.</p></article><article class="question"><h3>Console access.</h3><p>The administrator accesses the console with operator credentials. To get started, <a href="/login">sign in here</a>.</p></article></div></section>'
