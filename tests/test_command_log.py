from __future__ import annotations

import json
from datetime import timedelta

import pytest

from app.services.commands import CommandLogService
from app.util import iso, utcnow


async def test_command_log_record_and_tail(db):
    service = CommandLogService(db)
    log_id1 = await service.record(
        guild_id=10,
        channel_id=101,
        user_id=501,
        user_name="alice#0001",
        command="appardas help",
        options={"topic": "dev"},
        status="success",
    )
    assert log_id1 > 0

    log_id2 = await service.record(
        guild_id=10,
        channel_id=101,
        user_id=502,
        user_name="bob",
        command="errors",
        options=None,
        status="denied",
    )
    assert log_id2 > log_id1

    # Fetch tail
    rows = await service.tail(guild_id=10, limit=10)
    assert len(rows) == 2
    assert rows[0]["id"] == log_id2
    assert rows[0]["command"] == "errors"
    assert rows[0]["status"] == "denied"
    assert rows[0]["user_name"] == "bob"

    assert rows[1]["id"] == log_id1
    assert rows[1]["command"] == "appardas help"
    assert json.loads(rows[1]["options"]) == {"topic": "dev"}

    # Filter by command
    help_rows = await service.tail(guild_id=10, command="appardas help")
    assert len(help_rows) == 1
    assert help_rows[0]["id"] == log_id1

    # Filter by user
    bob_rows = await service.tail(guild_id=10, user_id=502)
    assert len(bob_rows) == 1
    assert bob_rows[0]["id"] == log_id2

    # Filter by status
    denied_rows = await service.tail(guild_id=10, status="denied")
    assert len(denied_rows) == 1
    assert denied_rows[0]["id"] == log_id2

    # Distinct commands
    distinct = await service.distinct_commands(guild_id=10)
    assert distinct == ["appardas help", "errors"]


async def test_command_log_set_status_and_prune(db):
    service = CommandLogService(db)
    log_id = await service.record(
        guild_id=20,
        channel_id=201,
        user_id=601,
        user_name="carol",
        command="settings prefix",
        options={"value": "!"},
        status="success",
    )

    # Update status to error
    updated = await service.set_status(log_id, "error")
    assert updated is True

    rows = await service.tail(guild_id=20)
    assert rows[0]["status"] == "error"

    # Test prune
    old_time = iso(utcnow() - timedelta(days=95))
    await db.execute("UPDATE command_log SET created_at = ? WHERE id = ?", (old_time, log_id))

    pruned = await service.prune(90)
    assert pruned == 1

    remaining = await service.tail(guild_id=20)
    assert len(remaining) == 0
