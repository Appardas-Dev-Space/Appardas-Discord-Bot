"""GitHub org/repo polling: one request per watch, grouped delivery, dedupe.

Org watching is the primitive: `/orgs/{org}/events` returns activity across
every repository in the organisation, so two PRs in two repos arrive in one
message from one request.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta
from typing import Any, Awaitable, Callable, Iterable
from urllib.parse import urlparse

import aiohttp
import discord

from ..config import Settings
from ..db import Database
from ..util import ago_text, iso, parse_iso, truncate, utcnow
from ..views.embeds import github_event_embed, github_summary_embed
from .audit import AuditService

log = logging.getLogger(__name__)

API_ROOT = "https://api.github.com"
EVENT_TYPES = ("releases", "pulls", "issues", "pushes")
DEFAULT_EVENTS = ",".join(EVENT_TYPES)
EVENT_MAP = {
    "ReleaseEvent": "releases",
    "PullRequestEvent": "pulls",
    "IssuesEvent": "issues",
    "PushEvent": "pushes",
}
SECTION_TITLES = {
    "pulls": "Pull requests",
    "releases": "Releases",
    "issues": "Issues",
    "pushes": "Pushes",
}
MAX_LINES_PER_SECTION = 3
_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


class GitHubError(ValueError):
    pass


class WatchLimitError(GitHubError):
    pass


def normalise_target(text: str) -> tuple[str, str]:
    """Return (target, target_type) from `org`, `org/repo` or a GitHub URL."""
    raw = (text or "").strip()
    if not raw:
        raise GitHubError("Give an org, `owner/repo`, or a github.com URL.")
    if raw.startswith("http://") or raw.startswith("https://"):
        parsed = urlparse(raw)
        if parsed.netloc.lower() not in ("github.com", "www.github.com"):
            raise GitHubError("Only github.com URLs are supported.")
        raw = parsed.path.strip("/")
    raw = raw.removesuffix(".git").strip("/")
    parts = [part for part in raw.split("/") if part]
    if not parts or len(parts) > 2:
        raise GitHubError("Expected `org`, `org/repo`, or `https://github.com/org/repo`.")
    if not all(_NAME_RE.match(part) for part in parts):
        raise GitHubError("That target contains characters GitHub does not allow.")
    if len(parts) == 2:
        return f"{parts[0]}/{parts[1]}".lower(), "repo"
    return parts[0].lower(), "org"


def normalise_events(values: Iterable[str] | None) -> str:
    chosen = [value for value in (values or []) if value in EVENT_TYPES]
    if not chosen:
        chosen = list(EVENT_TYPES)
    return ",".join(dict.fromkeys(chosen))


def _event_id(event: dict[str, Any]) -> int:
    try:
        return int(event.get("id") or 0)
    except (TypeError, ValueError):
        return 0


class GitHubService:
    def __init__(
        self,
        db: Database,
        config: Settings,
        audit: AuditService,
        status: Any | None = None,
    ) -> None:
        self.db = db
        self.config = config
        self.audit = audit
        self.status = status
        self._session: aiohttp.ClientSession | None = None
        self._backoff: dict[int, datetime] = {}
        self.rate_limit_remaining: int | None = None
        self.rate_limit_reset: datetime | None = None

    # --------------------------------------------------------------- session
    async def start(self) -> None:
        timeout = aiohttp.ClientTimeout(total=self.config.github_request_timeout_s)
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "appardas-discord-bot/0.4",
        }
        if self.config.github_token:
            headers["Authorization"] = f"Bearer {self.config.github_token}"
        self._session = aiohttp.ClientSession(timeout=timeout, headers=headers)

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    # ------------------------------------------------------------------ CRUD
    async def list_watches(self, guild_id: int) -> list[dict[str, Any]]:
        return await self.db.fetchall(
            "SELECT id, guild_id, target, target_type, channel_id, events, etag, cursor, "
            "last_polled, last_delivered, state, last_error, created_by "
            "FROM github_watches WHERE guild_id = ? ORDER BY target",
            (guild_id,),
        )

    async def get_watch(self, guild_id: int, target: str) -> dict[str, Any] | None:
        return await self.db.fetchone(
            "SELECT * FROM github_watches WHERE guild_id = ? AND target = ?",
            (guild_id, target),
        )

    async def watch_count(self, guild_id: int) -> int:
        return int(
            await self.db.scalar(
                "SELECT COUNT(*) FROM github_watches WHERE guild_id = ?", (guild_id,)
            )
            or 0
        )

    async def add_watch(
        self,
        *,
        guild_id: int,
        target: str,
        target_type: str,
        channel_id: int,
        events: str,
        created_by: int,
    ) -> dict[str, Any]:
        if await self.watch_count(guild_id) >= self.config.github_max_watches:
            raise WatchLimitError(
                f"This server already has {self.config.github_max_watches} GitHub watches. "
                "An org watch already covers every repository in the org."
            )
        existing = await self.get_watch(guild_id, target)
        if existing:
            raise GitHubError(f"`{target}` is already watched in this server.")
        await self.db.execute(
            "INSERT INTO github_watches "
            "(guild_id, target, target_type, channel_id, events, state, created_by) "
            "VALUES (?, ?, ?, ?, ?, 'quiet', ?)",
            (guild_id, target, target_type, channel_id, events, created_by),
        )
        watch = await self.get_watch(guild_id, target)
        assert watch is not None
        # Backfill policy: seed the cursor now, deliver nothing from the past.
        status, items, etag = await self._fetch(watch)
        if status == 200 and items:
            newest = max(_event_id(item) for item in items)
            await self.db.execute(
                "UPDATE github_watches SET cursor = ?, etag = ?, last_polled = ? WHERE id = ?",
                (str(newest), etag, iso(), watch["id"]),
            )
            watch = await self.get_watch(guild_id, target)
        await self.audit.record(
            guild_id=guild_id,
            actor_id=created_by,
            action="github.watch",
            target=target,
            detail={
                "target_type": target_type,
                "channel_id": channel_id,
                "events": events,
                "source": "bot",
                "backfilled": 0,
            },
        )
        assert watch is not None
        return watch

    async def remove_watch(self, guild_id: int, target: str, actor_id: int) -> bool:
        watch = await self.get_watch(guild_id, target)
        if not watch:
            return False
        await self.db.execute("DELETE FROM github_watches WHERE id = ?", (watch["id"],))
        self._backoff.pop(int(watch["id"]), None)
        await self.audit.record(
            guild_id=guild_id,
            actor_id=actor_id,
            action="github.unwatch",
            target=target,
            detail={"source": "bot"},
        )
        return True

    async def mark_state(self, watch_id: int, state: str, error: str | None = None) -> None:
        await self.db.execute(
            "UPDATE github_watches SET state = ?, last_error = ? WHERE id = ?",
            (state, truncate(error, 300) if error else None, watch_id),
        )

    # ------------------------------------------------------------------ HTTP
    async def _fetch(
        self, watch: dict[str, Any]
    ) -> tuple[int, list[dict[str, Any]], str | None]:
        """Return (status_code, events, etag). 0 means a transport failure."""
        if self._session is None:
            return 0, [], None
        target = watch["target"]
        if watch["target_type"] == "org":
            url = f"{API_ROOT}/orgs/{target}/events"
        else:
            url = f"{API_ROOT}/repos/{target}/events"
        headers = {}
        if watch.get("etag"):
            headers["If-None-Match"] = watch["etag"]
        try:
            async with self._session.get(
                url, params={"per_page": 30}, headers=headers
            ) as response:
                self._read_rate_headers(response.headers)
                if response.status == 304:
                    return 304, [], watch.get("etag")
                if response.status in (403, 429):
                    reset = response.headers.get("X-RateLimit-Reset")
                    if reset and reset.isdigit():
                        self.rate_limit_reset = datetime.fromtimestamp(
                            int(reset), tz=utcnow().tzinfo
                        )
                    return response.status, [], watch.get("etag")
                if response.status == 404:
                    return 404, [], watch.get("etag")
                if response.status >= 400:
                    return response.status, [], watch.get("etag")
                items = await response.json()
                if not isinstance(items, list):
                    return 0, [], watch.get("etag")
                return 200, items, response.headers.get("ETag")
        except asyncio.TimeoutError:
            log.warning("GitHub poll timed out for %s", target)
            return 0, [], watch.get("etag")
        except Exception:
            log.exception("GitHub poll failed for %s", target)
            return 0, [], watch.get("etag")

    def _read_rate_headers(self, headers: aiohttp.typedefs.LooseHeaders) -> None:
        remaining = headers.get("X-RateLimit-Remaining")
        if remaining is not None and str(remaining).isdigit():
            self.rate_limit_remaining = int(remaining)
            if self.status is not None:
                self.status.github_rate_remaining = self.rate_limit_remaining
        reset = headers.get("X-RateLimit-Reset")
        if reset is not None and str(reset).isdigit():
            self.rate_limit_reset = datetime.fromtimestamp(
                int(reset), tz=utcnow().tzinfo
            )

    def _rate_reset_text(self) -> str:
        if self.rate_limit_reset is None:
            return "in a few minutes"
        return f"at {self.rate_limit_reset.astimezone().strftime('%H:%M')}"

    # ------------------------------------------------------------- formatting
    def _line_for(self, event: dict[str, Any]) -> str | None:
        kind = EVENT_MAP.get(event.get("type", ""), "")
        payload = event.get("payload") or {}
        repo = (event.get("repo") or {}).get("name", "?")
        repo_short = repo.split("/")[-1]
        if kind == "pulls":
            pr = payload.get("pull_request") or {}
            number = pr.get("number", "?")
            title = truncate(pr.get("title") or "pull request", 90)
            action = payload.get("action", "updated")
            return f"#{number} {title} · {action} · {repo_short}"
        if kind == "issues":
            issue = payload.get("issue") or {}
            number = issue.get("number", "?")
            title = truncate(issue.get("title") or "issue", 90)
            action = payload.get("action", "updated")
            return f"#{number} {title} · {action} · {repo_short}"
        if kind == "releases":
            release = payload.get("release") or {}
            tag = release.get("tag_name") or release.get("name") or "release"
            name = truncate(release.get("name") or "", 80)
            suffix = f" · {name}" if name and name != tag else ""
            return f"{tag}{suffix} · {repo_short}"
        if kind == "pushes":
            ref = (payload.get("ref") or "").replace("refs/heads/", "")
            commits = len(payload.get("commits") or [])
            actor = (event.get("actor") or {}).get("login", "someone")
            return f"{actor} pushed {commits} commit{'s' if commits != 1 else ''} to {ref} · {repo_short}"
        return None

    def build_summary(
        self, watch: dict[str, Any], events: list[dict[str, Any]]
    ) -> tuple[discord.Embed | None, int]:
        allowed = set((watch.get("events") or DEFAULT_EVENTS).split(","))
        sections: dict[str, list[str]] = {kind: [] for kind in EVENT_TYPES}
        total = 0
        for event in events:
            kind = EVENT_MAP.get(event.get("type", ""))
            if not kind or kind not in allowed:
                continue
            line = self._line_for(event)
            if not line:
                continue
            total += 1
            if len(sections[kind]) < MAX_LINES_PER_SECTION:
                sections[kind].append(line)
        if total == 0:
            return None, 0
        if total == 1:
            # FEATURES §4: a release gets its own embed with notes and a link.
            release_event = next(
                (
                    event
                    for event in events
                    if EVENT_MAP.get(event.get("type", "")) == "releases"
                    and "releases" in allowed
                    and self._line_for(event)
                ),
                None,
            )
            if release_event is not None:
                release = (release_event.get("payload") or {}).get("release") or {}
                repo = (release_event.get("repo") or {}).get("name") or watch["target"]
                tag = release.get("tag_name") or release.get("name") or "release"
                url = release.get("html_url") or f"https://github.com/{repo}"
                return (
                    github_event_embed(
                        f"📦 {repo.split('/')[-1]} · {tag}",
                        url,
                        target=watch["target"],
                        release_notes=release.get("body"),
                    ),
                    1,
                )
        # Keep the type order deterministic and human.
        ordered = [
            (SECTION_TITLES[kind], lines)
            for kind, lines in sections.items()
            if lines
        ]
        embed = github_summary_embed(
            watch["target"], watch["target_type"], total, ordered
        )
        if total > sum(len(lines) for _, lines in ordered):
            embed.add_field(
                name="Also",
                value=f"{total - sum(len(lines) for _, lines in ordered)} more not shown",
                inline=False,
            )
        return embed, total

    # ---------------------------------------------------------------- polling
    async def poll_all(
        self,
        client: discord.Client,
        is_enabled: Callable[[int, str], Awaitable[bool]],
    ) -> dict[str, int]:
        result = {"polled": 0, "posted": 0, "quiet": 0, "skipped": 0, "failed": 0}
        watches = await self.db.fetchall(
            "SELECT * FROM github_watches WHERE state != 'missing' ORDER BY guild_id, target"
        )
        for watch in watches:
            guild_id = int(watch["guild_id"])
            if not await is_enabled(guild_id, "github"):
                result["skipped"] += 1
                continue
            until = self._backoff.get(int(watch["id"]))
            if until and until > utcnow():
                result["skipped"] += 1
                continue
            result["polled"] += 1
            status, items, etag = await self._fetch(watch)
            now = iso()
            if status == 304:
                await self._after_poll(watch, etag, "quiet", now, delivered=False)
                result["quiet"] += 1
                continue
            if status in (403, 429):
                self._backoff[int(watch["id"])] = utcnow() + timedelta(minutes=15)
                await self.mark_state(
                    int(watch["id"]),
                    "rate-limited",
                    f"rate limit reached, retrying {self._rate_reset_text()}",
                )
                await self._touch(watch["id"], now)
                result["failed"] += 1
                continue
            if status == 404:
                await self.mark_state(int(watch["id"]), "missing", "repository or organisation not found")
                await self._touch(watch["id"], now)
                log.warning("GitHub watch %s marked missing (404)", watch["target"])
                result["failed"] += 1
                continue
            if status != 200:
                self._backoff[int(watch["id"])] = utcnow() + timedelta(minutes=5)
                await self.mark_state(int(watch["id"]), "stale", "GitHub request failed")
                await self._touch(watch["id"], now)
                result["failed"] += 1
                continue

            cursor = int(watch["cursor"] or 0)
            if cursor == 0:
                newest = max((_event_id(item) for item in items), default=0)
                await self.db.execute(
                    "UPDATE github_watches SET cursor = ?, etag = ?, last_polled = ?, state = 'quiet' "
                    "WHERE id = ?",
                    (str(newest), etag, now, watch["id"]),
                )
                result["quiet"] += 1
                continue

            fresh = [item for item in items if _event_id(item) > cursor]
            embed, total = self.build_summary(watch, fresh)
            if embed is None or total == 0:
                await self._after_poll(watch, etag, "quiet", now, delivered=False)
                result["quiet"] += 1
                continue

            channel = client.get_channel(int(watch["channel_id"]))
            if channel is None:
                try:
                    channel = await client.fetch_channel(int(watch["channel_id"]))
                except Exception:
                    channel = None
            if channel is None:
                await self.mark_state(int(watch["id"]), "missing", "target channel no longer exists")
                await self._touch(watch["id"], now)
                result["failed"] += 1
                continue
            try:
                await channel.send(
                    embed=embed, allowed_mentions=discord.AllowedMentions.none()
                )
            except (discord.Forbidden, discord.NotFound) as exc:
                await self.mark_state(
                    int(watch["id"]), "missing", f"cannot post in channel: {type(exc).__name__}"
                )
                await self._touch(watch["id"], now)
                log.warning("GitHub watch %s cannot post: %s", watch["target"], exc)
                result["failed"] += 1
                continue
            except Exception:
                self._backoff[int(watch["id"])] = utcnow() + timedelta(minutes=5)
                await self.mark_state(int(watch["id"]), "stale", "posting failed")
                await self._touch(watch["id"], now)
                log.exception("GitHub watch %s post failed", watch["target"])
                result["failed"] += 1
                continue

            newest = max(_event_id(item) for item in fresh)
            await self.db.execute(
                "UPDATE github_watches SET cursor = ?, etag = ?, last_polled = ?, "
                "last_delivered = ?, state = 'ok', last_error = NULL WHERE id = ?",
                (str(newest), etag, now, now, watch["id"]),
            )
            await self.audit.record(
                guild_id=guild_id,
                actor_id=None,
                action="github.post",
                target=watch["target"],
                detail={"count": total, "source": "system"},
            )
            result["posted"] += 1
        return result

    async def _touch(self, watch_id: int, now: str) -> None:
        await self.db.execute(
            "UPDATE github_watches SET last_polled = ? WHERE id = ?", (now, watch_id)
        )

    async def _after_poll(
        self,
        watch: dict[str, Any],
        etag: str | None,
        state: str,
        now: str,
        *,
        delivered: bool,
    ) -> None:
        await self.db.execute(
            "UPDATE github_watches SET etag = ?, last_polled = ?, state = ? WHERE id = ?",
            (etag, now, state, watch["id"]),
        )

    async def test_post(
        self, client: discord.Client, watch: dict[str, Any]
    ) -> tuple[bool, str]:
        status, items, _ = await self._fetch(watch)
        if status == 304:
            return False, "No new events since the last poll — nothing to test with yet."
        if status != 200 or not items:
            return False, f"GitHub returned status {status or 'network error'}; no event to post."
        embed, total = self.build_summary(watch, items)
        if embed is None or total == 0:
            return False, "No events matched this watch's filters."
        channel = client.get_channel(int(watch["channel_id"]))
        if channel is None:
            try:
                channel = await client.fetch_channel(int(watch["channel_id"]))
            except Exception:
                return False, "The target channel no longer exists. Remove and re-add the watch."
        try:
            await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except discord.Forbidden:
            return False, "Missing Send Messages or Embed Links in the target channel."
        except Exception as exc:
            return False, f"Could not post: {type(exc).__name__}"
        return True, "Test post sent."

    # --------------------------------------------------------------- health
    def display_state(self, watch: dict[str, Any]) -> str:
        if watch.get("state") in ("missing", "rate-limited"):
            return watch["state"]
        last_polled = watch.get("last_polled")
        if last_polled:
            try:
                age = (utcnow() - parse_iso(last_polled)).total_seconds()
                if age > self.config.github_poll_interval_s * 2:
                    return "stale"
            except Exception:
                pass
        return watch.get("state") or "quiet"

    def last_poll_text(self, watch: dict[str, Any]) -> str:
        if not watch.get("last_polled"):
            return "never"
        try:
            return ago_text(parse_iso(watch["last_polled"]))
        except Exception:
            return "unknown"

    def last_delivery_text(self, watch: dict[str, Any]) -> str:
        if not watch.get("last_delivered"):
            return "nothing yet"
        try:
            return ago_text(parse_iso(watch["last_delivered"]))
        except Exception:
            return "unknown"
