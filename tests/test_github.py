from __future__ import annotations

from datetime import timedelta

import pytest

from app.services.github import (
    DEFAULT_EVENTS,
    GitHubError,
    GitHubService,
    normalise_events,
    normalise_target,
)
from app.util import iso, utcnow


def _service(cfg):
    return GitHubService(db=None, config=cfg, audit=None, status=None)  # type: ignore[arg-type]


def test_normalise_org_repo_and_url():
    assert normalise_target("appardas") == ("appardas", "org")
    assert normalise_target("Appardas/Bot") == ("appardas/bot", "repo")
    assert normalise_target("https://github.com/appardas/bot") == ("appardas/bot", "repo")
    assert normalise_target("https://github.com/appardas/bot.git") == ("appardas/bot", "repo")


def test_normalise_rejects_bad_shapes():
    for value in ("", "a/b/c", "https://gitlab.com/org/repo", "org/re po"):
        with pytest.raises(GitHubError):
            normalise_target(value)


def test_event_filter_defaults_to_all():
    assert normalise_events(None) == DEFAULT_EVENTS
    assert normalise_events(["pulls", "nonsense"]) == "pulls"
    assert normalise_events(["issues", "pulls", "issues"]) == "issues,pulls"


def test_grouping_two_repos_in_one_message(cfg):
    service = _service(cfg)
    watch = {
        "target": "appardas",
        "target_type": "org",
        "events": DEFAULT_EVENTS,
        "cursor": "1",
    }
    events = [
        {
            "id": "10",
            "type": "PullRequestEvent",
            "repo": {"name": "appardas/bot"},
            "payload": {
                "action": "opened",
                "pull_request": {"number": 43, "title": "Persist panels across restarts"},
            },
        },
        {
            "id": "11",
            "type": "PullRequestEvent",
            "repo": {"name": "appardas/cli"},
            "payload": {
                "action": "opened",
                "pull_request": {"number": 44, "title": "Retry GitHub polls on 5xx"},
            },
        },
        {
            "id": "12",
            "type": "ReleaseEvent",
            "repo": {"name": "appardas/bot"},
            "payload": {"release": {"tag_name": "v0.4.0", "name": "SQLite tuning"}},
        },
    ]
    embed, total = service.build_summary(watch, events)
    assert total == 3
    assert "appardas" in embed.title
    assert "#43" in embed.description
    assert "#44" in embed.description
    assert "v0.4.0" in embed.description


def test_grouping_ignores_unwatched_types(cfg):
    service = _service(cfg)
    watch = {"target": "appardas", "target_type": "org", "events": "releases"}
    events = [
        {
            "id": "10",
            "type": "PullRequestEvent",
            "repo": {"name": "appardas/bot"},
            "payload": {"action": "opened", "pull_request": {"number": 1, "title": "x"}},
        }
    ]
    embed, total = service.build_summary(watch, events)
    assert embed is None
    assert total == 0


def test_stale_state(cfg):
    service = _service(cfg)
    watch = {
        "state": "quiet",
        "last_polled": iso(utcnow() - timedelta(seconds=cfg.github_poll_interval_s * 3)),
    }
    assert service.display_state(watch) == "stale"
    watch["last_polled"] = iso(utcnow())
    assert service.display_state(watch) == "quiet"
    watch["state"] = "missing"
    assert service.display_state(watch) == "missing"


def test_is_newer_handles_sharded_github_id_spaces():
    from app.services.github import _cursor_key, _is_newer, _serialize_cursor

    # Case: Old CreateEvent has ID 25B (e.g. 25028689861 at 08:39 UTC)
    # New IssuesEvent has ID 18B (e.g. 18108572207 at 14:49 UTC)
    old_event = {"id": "25028689861", "created_at": "2026-10-24T08:39:46Z", "type": "CreateEvent"}
    new_event = {"id": "18108572207", "created_at": "2026-10-24T14:49:48Z", "type": "IssuesEvent"}

    cursor_str = _serialize_cursor(old_event)
    assert cursor_str == "2026-10-24T08:39:46Z|25028689861"

    cursor_time, cursor_id = _cursor_key(cursor_str)
    assert _is_newer(new_event, cursor_time, cursor_id) is True
    assert _is_newer(old_event, cursor_time, cursor_id) is False

    # Same timestamp tie-breaking by ID
    event_a = {"id": "100", "created_at": "2026-10-24T15:00:00Z"}
    event_b = {"id": "101", "created_at": "2026-10-24T15:00:00Z"}
    assert _is_newer(event_b, "2026-10-24T15:00:00Z", 100) is True
    assert _is_newer(event_a, "2026-10-24T15:00:00Z", 100) is False
