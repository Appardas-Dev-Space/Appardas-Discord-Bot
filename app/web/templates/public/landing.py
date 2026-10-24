"""A live operating record, with the heartbeat inside the headline."""

from __future__ import annotations

from typing import Any

from ..base import e
from ..widgets import heartbeat_svg, readings, renewal_block
from .commands import command_finder
from .getting_started import stages

SHORT_COGS = {"status": "Connection and runtime", "settings": "Config and audit", "github": "Repository updates", "dev": "Announcements and nudges", "utility": "Help and links"}


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
        ("What if it restarts?", "Saved settings, watches, pending announcements and dashboard sessions stay in SQLite. The heartbeat marks the time this process has actually observed."),
        ("Can I turn a cog off?", "GitHub and dev can be paused per server. Saved work stays put. Status, settings and utility stay available so the bot remains recoverable."),
        ("Who can use the console?", "Access is restricted to authorized operators. The preliminary administrator account configures servers, manages cog states, and audits activity. All operator actions are recorded."),
        ("What is the four-day clock?", "The hosting cycle needs a manual renewal. The owner marks it with /appardas renew; the clock shows what remains. It does not renew the host for you."),
        ("What gets recorded?", "Server changes go to the guild audit. Sign-ins and console changes also go to an administrator activity log with account, time, IP and the outcome. No analytics or page-view tracking."),
        ("What if this page is gone?", "The site and bot run in the same Python process on one port. A stopped process cannot serve a green status page. The host reports the outage."),
    )
    faq = ''.join(f'<article class="question"><h3 data-reveal>{e(question)}</h3><p data-reveal>{e(answer)}</p></article>' for question, answer in questions)
    return f'''<div data-live-status data-checked-at="{e(snapshot["checked_at"])}"><section class="hero"><h1 class="hero-title">A Discord bot,<br><span class="hero-ending" data-hero-ending>{ending}</span></h1><p class="hero-support">Exclusive operational Discord bot: GitHub watches, announcements, nudges, settings and live status in one process.</p><div class="hero-actions"><a class="primary" href="/commands">Read the commands</a><a class="plain-button" href="/getting-started">Deployment guide</a>{console_action}</div><div class="hero-telemetry"><div class="telemetry-card"><div class="telemetry-head"><span class="telemetry-label"><span class="live-dot"></span> 24h Live Heartbeat</span><span class="hero-summary" data-heartbeat-summary>{e(summary)}</span></div><div class="hero-media">{heartbeat_svg(heartbeat)}</div></div></div></section><section class="public-section" id="right-now"><h2 data-reveal>Right now.</h2><div class="live-grid"><div><div class="live-title"><p class="public-verdict {e(snapshot["status"])}" data-verdict>{e(snapshot["status"])}</p><p class="muted" data-reading="loaded">{loaded} / {len(snapshot["cogs"])} loaded</p></div><p class="live-note" data-reading="connection">{e(connection)}</p>{readings(snapshot, public=True)}<div class="runtime-cogs">{rows}</div><p class="live-note">Database: <span data-reading="database">{db}</span>. Offline means Discord is disconnected; degraded means the database, runtime or a recent scheduler run needs attention.</p><p class="freshness"><span data-freshness>Checked {e(snapshot["checked_at"][11:16])} UTC.</span> <a href="/#right-now">Refresh</a></p><noscript><p class="live-note">These are the readings at page load. Refresh for an update.</p></noscript></div>{renewal_block(snapshot["renewal"]["last_renewed_at"], public=True)}</div></section></div><section class="public-section"><h2 data-reveal>What it does.</h2><p class="feature-statement" data-reveal>Check the connection <span class="inline-ui" data-ui>status · /ping</span>, set the rules <span class="inline-ui" data-ui>settings · prefix !</span>, follow the work <span class="inline-ui" data-ui>github · watch</span>, tell the team <span class="inline-ui" data-ui>dev · announce</span>, and find your way back <span class="inline-ui" data-ui>utility · /help</span>. Five small cogs, one shared record.</p></section>{command_finder(manifest)}<section class="public-section"><h2 data-reveal>Operations and capabilities.</h2>{stages(invite)}</section><section class="public-section"><h2 data-reveal>Questions.</h2><div class="questions">{faq}</div></section>'''
