"""A live operating record, with the heartbeat inside the headline."""

from __future__ import annotations

from typing import Any

from ..base import e
from ..widgets import heartbeat_svg, readings, renewal_block
from .commands import command_finder

SHORT_COGS = {
    "status": "System health & gateway diagnostics",
    "settings": "Configuration & audit logging",
    "github": "Internal repository & release tracking",
    "dev": "Broadcasts & scheduled announcements",
    "utility": "Command directory & documentation",
}


def landing_page(snapshot: dict[str, Any], manifest: dict[str, Any], invite: str | None, account: object | None = None) -> str:
    heartbeat = snapshot["heartbeat"]
    summary = heartbeat["summary"]
    ending = "no gaps." if "no gaps" in summary else "checking in." if "no readings" in summary else "gaps recorded."
    console_action = '<a class="plain-button" href="/dashboard">Open Console</a>' if account else '<a class="plain-button" href="/login">Console sign in</a>'
    rows = ''.join(f'<div class="runtime-cog"><a href="/commands/{e(cog["key"])}">{e(cog["key"])}</a><p>{SHORT_COGS[cog["key"]]}</p><span class="state" data-reading="cog-{e(cog["key"])}">{ {"active": "active", "faulted": "unavailable", "loading": "starting"}[cog["state"]] }</span></div>' for cog in snapshot["cogs"])
    connection = snapshot["discord"]["state"]
    connection = "Connected to Discord" if connection == "connected" else f"{connection.capitalize()} to Discord"
    db = "healthy" if snapshot["db"]["healthy"] else "unhealthy"
    loaded = sum(cog["state"] == "active" for cog in snapshot["cogs"])
    questions = (
        ("Is this bot publicly available?", "No. This bot (appa-dev) is proprietary software developed strictly for internal operations at Appardas. Public invites are disabled and the bot cannot be added to external Discord servers."),
        ("Who can access the web console?", "Console access is restricted to verified Appardas staff and administrators. Authorized operators manage announcements, bot presence, and inspect command audit logs."),
        ("What activity is recorded?", "Command executions (user ID, timestamp, channel, parameters, outcome) and administrative console changes are logged in the internal audit database for operational compliance. No third-party tracking or analytics are used."),
        ("How does state persist across restarts?", "Server configurations, scheduled announcements, watch settings, and operator sessions persist in SQLite. State is preserved automatically across container restarts."),
        ("Can individual modules be paused?", "Authorized administrators can pause non-critical modules (such as GitHub watches or development utilities) per server while core status, settings, and utility systems remain active."),
        ("How is service availability monitored?", "This web service runs directly within the bot's runtime process. If the bot stops or encounters an infrastructure outage, this status portal immediately reflects the downtime."),
    )
    faq = ''.join(f'<article class="question"><h3 data-reveal>{e(question)}</h3><p data-reveal>{e(answer)}</p></article>' for question, answer in questions)
    overview_cards = (
        '<article data-reveal>'
        '<h3>Proprietary & Private.</h3>'
        '<p>This bot is developed solely for Appardas company infrastructure. Public invitation is disabled; deployment is strictly restricted to company-managed Discord servers.</p>'
        '<p class="start-note">Access is governed by internal organizational roles.</p>'
        '</article>'
        '<article data-reveal>'
        '<h3>Role-Based Access.</h3>'
        '<p>Administrative and operational commands require designated Discord roles or authenticated console sessions. Configuration changes are logged for accountability.</p>'
        '<code class="command-line">/settings config github_channel #releases</code>'
        '<code class="command-line">/settings prefix !</code>'
        '</article>'
        '<article data-reveal>'
        '<h3>Audited Operations.</h3>'
        '<p>All automated tasks, GitHub repository watches, and staff broadcasts maintain structured audit trails and persistent configuration states in SQLite.</p>'
        '<code class="command-line">/github watch appardas/bot #releases</code>'
        '<code class="command-line">/status</code>'
        '</article>'
    )
    return f'''<div data-live-status data-checked-at="{e(snapshot["checked_at"])}"><section class="hero"><h1 class="hero-title">Appardas operations,<br><span class="hero-ending" data-hero-ending>{ending}</span></h1><p class="hero-support">Official proprietary Discord service for Appardas company operations. Manages internal repository notifications, scheduled company broadcasts, operational reminders, and service health monitoring.</p><div class="hero-actions"><a class="primary" href="/commands">Command directory</a><a class="plain-button" href="/#right-now">System status</a>{console_action}</div><div class="hero-telemetry"><div class="telemetry-card"><div class="telemetry-head"><span class="telemetry-label"><span class="live-dot"></span> 24h Live Heartbeat</span><span class="hero-summary" data-heartbeat-summary>{e(summary)}</span></div><div class="hero-media">{heartbeat_svg(heartbeat)}</div></div></div></section><section class="public-section" id="right-now"><h2 data-reveal>Operational status.</h2><div class="live-grid"><div><div class="live-title"><p class="public-verdict {e(snapshot["status"])}" data-verdict>{e(snapshot["status"])}</p><p class="muted" data-reading="loaded">{loaded} / {len(snapshot["cogs"])} loaded</p></div><p class="live-note" data-reading="connection">{e(connection)}</p>{readings(snapshot, public=True)}<div class="runtime-cogs">{rows}</div><p class="live-note">Database: <span data-reading="database">{db}</span>. The bot runtime and telemetry service execute in a unified process. Offline indicates Discord connectivity loss; degraded indicates database or scheduler exceptions requiring attention.</p><p class="freshness"><span data-freshness>Checked {e(snapshot["checked_at"][11:16])} UTC.</span> <a href="/#right-now">Refresh</a></p><noscript><p class="live-note">These are the readings at page load. Refresh for an update.</p></noscript></div>{renewal_block(snapshot["renewal"]["last_renewed_at"], public=True)}</div></section></div><section class="public-section"><h2 data-reveal>Core internal modules.</h2><p class="feature-statement" data-reveal>Designed for internal company workflows: connection diagnostics <span class="inline-ui" data-ui>status · /ping</span>, server configuration <span class="inline-ui" data-ui>settings · prefix !</span>, repository event tracking <span class="inline-ui" data-ui>github · watch</span>, staff broadcasts & scheduled notices <span class="inline-ui" data-ui>dev · announce</span>, and operator utility references <span class="inline-ui" data-ui>utility · /help</span>. All operations execute within a single secure process.</p></section>{command_finder(manifest)}<section class="public-section"><h2 data-reveal>System architecture & access policy.</h2><div class="start-grid">{overview_cards}</div></section><section class="public-section"><h2 data-reveal>System information & policies.</h2><div class="questions">{faq}</div></section>'''
