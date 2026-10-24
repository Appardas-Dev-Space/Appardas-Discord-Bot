"""Shared, server-rendered readings. No requests or database access."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from ...util import ago_text, parse_iso, remaining_text, utcnow
from .base import e


def heartbeat_svg(series: dict[str, Any]) -> str:
    mapping = {"on": "1", "low": "l", "off": "0", "none": "-", "now": "n"}
    ticks = series.get("ticks", "-" * 288)
    if not isinstance(ticks, str):
        ticks = "".join(mapping.get(tick, "-") for tick in ticks)
    rects = "".join(f'<rect x="{index * 2}" y="{38 if tick == "-" else 20 if tick == "l" else 0}" width="1" height="{2 if tick == "-" else 20 if tick == "l" else 40}" class="b{tick}"/>' for index, tick in enumerate(ticks[:288]) if tick in "1l0-n")
    return f'<svg class="heartbeat" data-heartbeat data-ticks="{e(ticks)}" role="img" aria-label="{e(series.get("summary", "24 hours, no readings yet"))}" viewBox="0 0 575 40" preserveAspectRatio="none" shape-rendering="crispEdges">{rects}</svg>'


def renewal_block(renewed: str | None, *, public: bool = False, now: datetime | None = None) -> str:
    heading = "h3" if public else "h2"
    body = '<p class="clock-top">Renewal not tracked</p><p class="clock-foot">The owner starts the four-day clock with <code>/appardas renew</code> in Discord.</p>'
    if renewed:
        start = parse_iso(renewed)
        now = now or utcnow()
        deadline = start + timedelta(days=4)
        remaining = (deadline - now).total_seconds()
        elapsed = max(0, (now - start).total_seconds())
        progress = min(4.0, elapsed / 86400)
        segments = "".join(f'<progress max="1" value="{max(0, min(1, progress - index)):.3f}" aria-label="Cycle day {index + 1}"></progress>' for index in range(4))
        label = "Overdue" if remaining < 0 else "Time left"
        body = f'<div class="clock-top"><p>Day {min(4, int(progress) + 1)} of 4</p><div><p class="clock-left">{e(remaining_text(abs(remaining)))}</p><p class="muted">{label}</p></div></div><div class="clock-track">{segments}</div><div class="clock-foot"><p>Renew by {e(deadline.strftime("%d %b, %H:%M UTC"))}</p><p>Last renewed <time datetime="{e(renewed)}">{e(start.strftime("%d %b, %H:%M UTC"))}</time></p></div>'
    return f'<section class="{"public-renewal" if public else "panel"}" data-live-fragment="renewal"><{heading}>Renewal clock</{heading}>{body}</section>'


def readings(snapshot: dict[str, Any], *, public: bool = False) -> str:
    runtime = snapshot.get("runtime", snapshot)
    def number(value: Any, suffix: str, places: int = 0) -> str:
        return f"{value:.{places}f} {suffix}" if isinstance(value, (int, float)) else "—"
    uptime = int(snapshot.get("uptime_s", 0))
    values = (("gateway", "Gateway", number(snapshot.get("discord", {}).get("gateway_latency_ms"), "ms")), ("lag", "Event-loop lag", number(runtime.get("loop_lag_ms"), "ms", 1)), ("rss", "Memory", number(runtime.get("mem_rss_mb"), "MB")), ("uptime", "Uptime", f"{uptime // 3600}h {uptime % 3600 // 60}m"))
    return f'<dl class="{"readings" if public else "kpis"}" data-live-fragment="readings">' + "".join(f'<div class="{"reading" if public else "kpi"}"><dt>{label}</dt><dd data-reading="{key}">{e(value)}</dd></div>' for key, label, value in values) + '</dl>'


def stamp(value: str | None) -> str:
    if not value:
        return '<span class="muted">never</span>'
    try:
        label = ago_text(parse_iso(value))
    except (ValueError, TypeError):
        label = value
    return f'<time datetime="{e(value)}" title="{e(value)}">{e(label)}</time>'


def json_detail(value: str | dict | None) -> str:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            pass
    return e(json.dumps(value, indent=2, ensure_ascii=False))
