"""Runtime metrics and the cached snapshot behind `/status` and the dashboard.

Nothing here is queried per HTTP request: `snapshot()` refreshes at most once
every 60 seconds and serves the same dict to every caller in between.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import platform
import resource
import time
from datetime import datetime, timedelta
from typing import Any

from ..config import Settings
from ..cogs.registry import COGS
from ..db import Database
from ..util import iso, parse_iso, utcnow, window_start
from ..version import VERSION

log = logging.getLogger(__name__)

SNAPSHOT_TTL_S = 60
HEARTBEAT_BUCKET_S = 300
HEARTBEAT_COUNT = 288  # 24 hours at 5-minute resolution
MEMORY_WARN_MB = 350
MEMORY_SHED_MB = 420


def verdict(snapshot: dict[str, Any]) -> str:
    """The same health precedence for the public page, JSON, and private console."""
    if snapshot.get("discord", {}).get("state") != "connected":
        return "offline"
    runtime = snapshot.get("runtime", snapshot)
    if (
        not snapshot.get("db", {}).get("healthy", False)
        or (runtime.get("loop_lag_ms") or 0) > 50
        or (runtime.get("mem_rss_mb") or 0) > MEMORY_WARN_MB
    ):
        return "degraded"
    error_at = snapshot.get("scheduler", {}).get("last_error_at")
    if error_at:
        try:
            if (utcnow() - parse_iso(error_at)).total_seconds() <= 900:
                return "degraded"
        except (ValueError, TypeError):
            pass
    return "ok"


class StatusService:
    def __init__(self, db: Database, config: Settings, settings_service: Any) -> None:
        self.db = db
        self.config = config
        self.settings = settings_service
        self.started_at = utcnow()
        self._client: Any | None = None

        self._snapshot: dict[str, Any] | None = None
        self._refreshed_at: float = 0.0
        self._refresh_lock = asyncio.Lock()
        self._ever_connected = False
        self._heartbeat_cache: tuple[int, dict[str, Any]] | None = None

        self._cpu_last_wall = time.monotonic()
        self._cpu_last_proc = time.process_time()
        self.cpu_pct = 0.0
        self.loop_lag_ms = 0.0
        self.loop_lag_peak_ms = 0.0
        self.rest_latency_ms: float | None = None
        self.github_rate_remaining: int | None = None
        self.db_latency_ms: float | None = None
        self.scheduler: dict[str, Any] = {
            "announce_dispatch_last_run": None,
            "github_poll_last_run": None,
            "prune_last_run": None,
            "last_error": None,
            "last_error_at": None,
        }
        self._tasks: list[asyncio.Task[Any]] = []

    # --------------------------------------------------------------- wiring
    def bind(self, client: Any) -> None:
        self._client = client

    def mark_loop_error(self, message: str) -> None:
        self.scheduler["last_error"] = message[:300]
        self.scheduler["last_error_at"] = iso()

    def mark_scheduler(self, name: str, when: datetime | None = None) -> None:
        self.scheduler[f"{name}_last_run"] = iso(when)

    def set_rest_latency(self, ms: float) -> None:
        self.rest_latency_ms = round(ms, 1)

    # -------------------------------------------------------------- monitors
    async def start_monitors(self) -> None:
        self._tasks.append(asyncio.create_task(self._loop_lag_loop(), name="loop-lag"))
        self._tasks.append(asyncio.create_task(self._refresh_loop(), name="status-refresh"))

    async def stop_monitors(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    async def _loop_lag_loop(self) -> None:
        interval = 1.0
        while True:
            started = time.monotonic()
            await asyncio.sleep(interval)
            lag = max(0.0, (time.monotonic() - started - interval) * 1000)
            self.loop_lag_ms = round(lag, 1)
            self.loop_lag_peak_ms = round(max(self.loop_lag_peak_ms, lag), 1)

    async def _refresh_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(SNAPSHOT_TTL_S)
                await self.refresh()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("status refresh failed")

    def _sample_cpu(self) -> float:
        now = time.monotonic()
        proc = time.process_time()
        elapsed = now - self._cpu_last_wall
        if elapsed <= 0:
            return self.cpu_pct
        self.cpu_pct = round(((proc - self._cpu_last_proc) / elapsed) * 100, 1)
        self._cpu_last_wall = now
        self._cpu_last_proc = proc
        return self.cpu_pct

    def _rss_mb(self) -> float:
        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # macOS reports bytes; Linux reports KiB.
        mb = peak / (1024 * 1024) if platform.system() == "Darwin" else peak / 1024
        return round(mb, 1)

    def _discord_state(self) -> str:
        client = self._client
        if client is None:
            return "connecting"
        if client.is_closed():
            return "disconnected"
        if not client.is_ready():
            return "reconnecting" if self._ever_connected else "connecting"
        self._ever_connected = True
        return "connected"

    def _guilds(self) -> int:
        if self._client is None:
            return 0
        return len(self._client.guilds)

    def _members_cached(self) -> int:
        if self._client is None:
            return 0
        return sum(len(getattr(guild, "members", []) or []) for guild in self._client.guilds)

    def _gateway_latency(self) -> float | None:
        if self._client is None or not self._client.is_ready():
            return None
        latency = self._client.latency
        if latency is None or latency == float("inf"):
            return None
        return round(latency * 1000, 1)

    # --------------------------------------------------------------- refresh
    async def refresh(self) -> dict[str, Any]:
        async with self._refresh_lock:
            started = time.perf_counter()
            db_ok = await self.db.health_check()
            self.db_latency_ms = round((time.perf_counter() - started) * 1000, 1)
            modules: dict[str, Any] = {
                "announcements": {"pending": 0, "sent_7d": 0, "failed_7d": 0},
                "nudge": {"sent_7d": 0, "delivered_7d": 0, "optouts": 0},
                "github": {"watches": 0, "posted_7d": 0, "rate_limit_remaining": self.github_rate_remaining},
            }
            last_write = self.db.last_write_at
            if db_ok:
                try:
                    week = window_start(7 * 86400)
                    ann = await self.db.fetchone(
                        "SELECT "
                        "COALESCE(SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END), 0) AS pending, "
                        "COALESCE(SUM(CASE WHEN status = 'sent' AND sent_at >= ? THEN 1 ELSE 0 END), 0) AS sent_7d, "
                        "COALESCE(SUM(CASE WHEN status = 'failed' AND created_at >= ? THEN 1 ELSE 0 END), 0) AS failed_7d "
                        "FROM announcements",
                        (week, week),
                    )
                    nudge = await self.db.fetchone(
                        "SELECT "
                        "COALESCE(SUM(CASE WHEN created_at >= ? THEN 1 ELSE 0 END), 0) AS sent_7d, "
                        "COALESCE(SUM(CASE WHEN created_at >= ? AND delivered = 1 THEN 1 ELSE 0 END), 0) AS delivered_7d "
                        "FROM nudges",
                        (week, week),
                    )
                    optouts = await self.db.scalar("SELECT COUNT(*) FROM nudge_optouts")
                    watches = await self.db.scalar("SELECT COUNT(*) FROM github_watches")
                    posts = await self.db.scalar(
                        "SELECT COUNT(*) FROM audit_log WHERE action = 'github.post' AND created_at >= ?",
                        (week,),
                    )
                    modules = {
                        "announcements": dict(ann or {}),
                        "nudge": {
                            "sent_7d": int((nudge or {}).get("sent_7d") or 0),
                            "delivered_7d": int((nudge or {}).get("delivered_7d") or 0),
                            "optouts": int(optouts or 0),
                        },
                        "github": {
                            "watches": int(watches or 0),
                            "posted_7d": int(posts or 0),
                            "rate_limit_remaining": self.github_rate_remaining,
                        },
                    }
                except Exception:
                    log.exception("module counters failed")
                    db_ok = False

            self._sample_cpu()
            payload = {
                "status": "ok" if db_ok else "degraded",
                "version": VERSION,
                "checked_at": iso(),
                "boot_stage": getattr(self._client, "boot_stage", "discord"),
                "environment": self.config.environment,
                "started_at": iso(self.started_at),
                "uptime_s": int((utcnow() - self.started_at).total_seconds()),
                "mem_rss_mb": self._rss_mb(),
                "cpu_pct": self.cpu_pct,
                "loop_lag_ms": self.loop_lag_ms,
                "loop_lag_peak_ms": self.loop_lag_peak_ms,
                "discord": {
                    "state": self._discord_state(),
                    "gateway_latency_ms": self._gateway_latency(),
                    "rest_latency_ms": self.rest_latency_ms,
                    "guilds": self._guilds(),
                    "members_cached": self._members_cached(),
                },
                "db": {
                    "healthy": db_ok,
                    "size_bytes": self.db.size_bytes(),
                    "last_write_at": last_write,
                    "latency_ms": self.db_latency_ms,
                },
                "modules": modules,
                "scheduler": dict(self.scheduler),
                "github_poll_interval_s": self.config.github_poll_interval_s,
                "pid": os.getpid(),
                "github_token": bool(self.config.github_token),
            }
            payload["status"] = verdict(payload)
            if not db_ok:
                payload["reason"] = "db"
            self._snapshot = payload
            self._refreshed_at = time.monotonic()

            if payload["mem_rss_mb"] > MEMORY_WARN_MB:
                log.warning("RSS %.1f MB exceeds the %.0f MB warning line", payload["mem_rss_mb"], MEMORY_WARN_MB)
            if payload["mem_rss_mb"] > MEMORY_SHED_MB:
                log.error(
                    "RSS %.1f MB above %.0f MB — dropping caches",
                    payload["mem_rss_mb"],
                    MEMORY_SHED_MB,
                )
                with contextlib.suppress(Exception):
                    await self.db.execute("PRAGMA shrink_memory")
            return payload

    async def snapshot(self) -> dict[str, Any]:
        if self._snapshot is None:
            return await self.refresh()
        if (time.monotonic() - self._refreshed_at) > SNAPSHOT_TTL_S:
            try:
                return await self.refresh()
            except Exception:
                log.exception("serving last reading after a failed status refresh")
        return self._snapshot

    def public_cogs(self) -> list[dict[str, str]]:
        client = self._client
        faults = getattr(client, "cog_faults", {}) if client else {}
        return [
            {"key": key, "state": "faulted" if key in faults else "active" if client and client.get_cog(meta.class_name) is not None else "loading"}
            for key, meta in COGS.items()
        ]

    async def public_snapshot(self) -> dict[str, Any]:
        snapshot = await self.snapshot()
        heartbeat = await self.heartbeat_series(24)
        try:
            renewed = await self.db.get_meta("last_renewed_at")
            due = iso(parse_iso(renewed) + timedelta(days=4)) if renewed else None
        except Exception:
            renewed, due = None, None
        mapping = {"on": "1", "low": "l", "off": "0", "none": "-", "now": "n"}
        return {
            "status": verdict(snapshot), "checked_at": snapshot["checked_at"], "version": VERSION,
            "uptime_s": snapshot["uptime_s"],
            "discord": {key: snapshot["discord"][key] for key in ("state", "gateway_latency_ms", "guilds")},
            "runtime": {key: snapshot[key] for key in ("loop_lag_ms", "mem_rss_mb")},
            "db": {key: snapshot["db"][key] for key in ("healthy", "latency_ms")},
            "cogs": self.public_cogs(),
            "heartbeat": {"hours": 24, "bucket_s": HEARTBEAT_BUCKET_S, "legend": {"1": "on", "l": "low", "0": "gap", "-": "before start", "n": "now"}, "ticks": "".join(mapping[tick] for tick in heartbeat["ticks"]), "summary": heartbeat["summary"]},
            "renewal": {"last_renewed_at": renewed, "due_at": due},
        }

    async def health(self) -> dict[str, Any]:
        """Liveness only. Never touches SQLite."""
        return {
            "status": "ok",
            "discord": self._discord_state(),
            "uptime_s": int((utcnow() - self.started_at).total_seconds()),
            "version": VERSION,
        }

    # ------------------------------------------------------------- heartbeat
    async def record_heartbeat(self, ok: bool = True) -> None:
        bucket = int(utcnow().timestamp() // HEARTBEAT_BUCKET_S) * HEARTBEAT_BUCKET_S
        bucket_at = iso(datetime.fromtimestamp(bucket, tz=utcnow().tzinfo))
        try:
            await self.db.execute(
                "INSERT INTO heartbeats (bucket_at, ok) VALUES (?, ?) "
                "ON CONFLICT(bucket_at) DO UPDATE SET ok = excluded.ok",
                (bucket_at, 1 if ok else 0),
            )
            self._heartbeat_cache = None
            if self._client is not None:
                self._client.event_bus.publish({"type": "heartbeat"})
        except Exception:
            log.exception("heartbeat write failed")

    async def heartbeat_series(self, hours: int = 24) -> dict[str, Any]:
        count = int(hours * 3600 / HEARTBEAT_BUCKET_S)
        now = utcnow()
        current = int(now.timestamp() // HEARTBEAT_BUCKET_S) * HEARTBEAT_BUCKET_S
        if hours == 24 and self._heartbeat_cache and self._heartbeat_cache[0] == current:
            return self._heartbeat_cache[1]
        start = current - (count - 1) * HEARTBEAT_BUCKET_S
        started_bucket = int(self.started_at.timestamp() // HEARTBEAT_BUCKET_S) * HEARTBEAT_BUCKET_S
        try:
            rows = await self.db.fetchall(
                "SELECT bucket_at, ok FROM heartbeats WHERE bucket_at >= ? ORDER BY bucket_at",
                (iso(datetime.fromtimestamp(start, tz=now.tzinfo)),),
            )
        except Exception:
            rows = []
        seen = {row["bucket_at"]: bool(row["ok"]) for row in rows}
        ticks: list[str] = []
        gaps: list[tuple[int, int]] = []
        gap_start: int | None = None
        for index in range(count):
            stamp = start + index * HEARTBEAT_BUCKET_S
            key = iso(datetime.fromtimestamp(stamp, tz=now.tzinfo))
            value = seen.get(key)
            if value is None and stamp < started_bucket:
                ticks.append("none")
            elif value is None:
                ticks.append("off")
                if gap_start is None:
                    gap_start = index
            else:
                if index == count - 1:
                    ticks.append("now")
                elif value:
                    ticks.append("on")
                else:
                    ticks.append("low")
                if gap_start is not None:
                    gaps.append((gap_start, index - 1))
                    gap_start = None
        if gap_start is not None:
            gaps.append((gap_start, count - 1))

        summary = f"{hours} hours, no gaps"
        # Buckets before this process started cannot have a heartbeat. Counting
        # them as a gap made a freshly restarted bot claim it had been down.
        live_gaps = [
            gap
            for gap in gaps
            if start + gap[1] * HEARTBEAT_BUCKET_S >= started_bucket
        ]
        if live_gaps:
            first, last = max(live_gaps, key=lambda pair: pair[1] - pair[0])
            first = max(first, int((started_bucket - start) / HEARTBEAT_BUCKET_S))
            gap_from = datetime.fromtimestamp(start + first * HEARTBEAT_BUCKET_S, tz=now.tzinfo)
            gap_to = datetime.fromtimestamp(start + (last + 1) * HEARTBEAT_BUCKET_S, tz=now.tzinfo)
            summary = (
                f"{'one gap' if len(live_gaps) == 1 else str(len(live_gaps)) + ' gaps; longest'}, {gap_from.strftime('%H:%M')}–{gap_to.strftime('%H:%M')}, "
                "process not heartbeating"
            )
        if not seen:
            summary = f"{hours} hours, no readings yet"
        elif started_bucket > start and not live_gaps:
            summary = f"readings since {self.started_at.strftime('%H:%M')} UTC, no gaps"
        result = {
            "ticks": ticks,
            "summary": summary,
            "hours": hours,
            "started_at": iso(datetime.fromtimestamp(start, tz=now.tzinfo)),
        }
        if hours == 24:
            self._heartbeat_cache = (current, result)
        return result

    # ----------------------------------------------------------------- prune
    async def prune(self) -> dict[str, int]:
        counts = {"audit": 0, "activity": 0, "sessions": 0, "nudges": 0, "announcements": 0, "heartbeats": 0}
        try:
            counts["audit"] = await self.settings.audit.prune(self.config.audit_retention_days)
            from .activity import ActivityService
            from ..web.sessions import Sessions
            counts["activity"] = await ActivityService(self.db).prune(self.config.activity_retention_days)
            counts["sessions"] = await Sessions(self.db, self.config).prune()
            cursor = await self.db.execute(
                "DELETE FROM nudges WHERE created_at < ?",
                (iso(utcnow() - timedelta(days=self.config.nudge_retention_days)),),
            )
            counts["nudges"] = int(cursor.rowcount or 0)
            cursor = await self.db.execute(
                "DELETE FROM announcements WHERE status IN ('sent', 'cancelled', 'failed') AND created_at < ?",
                (iso(utcnow() - timedelta(days=30)),),
            )
            counts["announcements"] = int(cursor.rowcount or 0)
            cursor = await self.db.execute(
                "DELETE FROM heartbeats WHERE bucket_at < ?",
                (iso(utcnow() - timedelta(hours=48)),),
            )
            counts["heartbeats"] = int(cursor.rowcount or 0)
            self.mark_scheduler("prune")
        except Exception:
            log.exception("prune failed")
            self.mark_loop_error("prune failed")
        return counts
