from __future__ import annotations

import json
from datetime import timedelta

import pytest

from app.services.accounts import AccountsService
from app.services.activity import ActivityService, redact, safe_json
from app.util import iso, utcnow


class Request(dict):
    remote = "127.0.0.1"
    headers = {"User-Agent": "a" * 500, "X-Forwarded-For": "203.0.113.7, 127.0.0.1"}


async def test_activity_redacts_at_rest_and_preserves_actor_history(db):
    account = await AccountsService(db).by_discord_id(1)
    request = Request(request_id="0123456789abcdef", account=account)
    service = ActivityService(db)
    await service.record(request=request, action="config.set", guild_id=1, before={"prefix": "!"}, after={"prefix": "?"}, detail={"csrf": "NEVER", "nested": {"accessToken": "NEVER", "cookie": "NEVER", "reason": "kept"}})
    row = (await service.query({}))[0]
    assert row["request_id"] == "0123456789abcdef" and row["account_label"] == account.name
    assert row["ip"] == "127.0.0.1" and len(row["user_agent"]) == 200
    assert "NEVER" not in str(row)
    assert json.loads(row["before_json"]) == {"prefix": "!"}
    await db.execute("UPDATE accounts SET name = 'Renamed' WHERE id = ?", (account.id,))
    assert (await service.query({}))[0]["account_label"] == account.name
    assert redact({"password": "x", "session_id": "x", "authorizationCode": "x", "ok": "y"}) == {"ok": "y"}
    assert json.loads(safe_json({str(i): "a" * 500 for i in range(20)}))["truncated"]


async def test_activity_filters_utc_day_bounds_cursor_and_proxy(db):
    service = ActivityService(db, trust_proxy=True)
    request = Request(request_id="0123456789abcdef", discord_user_id=123)
    await service.record(request=request, action="auth.login.denied_unknown", outcome="denied")
    first = (await service.query({}))[0]
    assert first["account_id"] is None and first["discord_user_id"] == 123
    assert first["ip"] == "203.0.113.7"
    request["request_id"] = "abcdef0123456789"
    await service.record(request=request, action="config.set", guild_id=1)
    await db.execute("UPDATE activity_log SET created_at = '2026-10-23T23:59:59Z' WHERE id = ?", (first["id"],))
    filters = service.filters({"account": "anonymous", "action": "auth.login.denied_unknown", "outcome": "denied", "guild": "global", "from": "2026-10-23", "to": "2026-10-23"})
    assert [row["id"] for row in await service.query(filters)] == [first["id"]]
    assert await service.query({"before": str(first["id"])}) == []
    with pytest.raises(ValueError):
        service.filters({"from": "2026-11-01", "to": "2026-10-01"})
    with pytest.raises(ValueError):
        service.filters({"guild": "1 OR 1=1"})


async def test_retention_guard_and_pruning(db):
    service = ActivityService(db)
    request = Request(request_id="0123456789abcdef")
    await service.record(request=request, action="auth.login.error", outcome="error")
    await db.execute("UPDATE activity_log SET created_at = ?", (iso(utcnow() - timedelta(days=181)),))
    with pytest.raises(ValueError):
        await service.prune(1)
    assert await service.prune(180) == 1
    assert await service.query({}) == []
