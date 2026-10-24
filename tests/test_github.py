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
