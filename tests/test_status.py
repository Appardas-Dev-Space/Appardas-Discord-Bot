from __future__ import annotations

from app.db import Database
from app.services.status import StatusService


async def test_heartbeat_series_does_not_invent_prestart_gaps(
    cfg, settings_service, tmp_path
):
    database = Database(tmp_path / "heartbeat.db")
    await database.connect()
    try:
        service = StatusService(database, cfg, settings_service)
        await service.record_heartbeat(True)
        series = await service.heartbeat_series()
        assert series["ticks"][-1] == "now"
        # A process that started minutes ago must not claim a 20-hour outage.
        assert "no gaps" in series["summary"]
    finally:
        await database.close()


async def test_presence_lifecycle(cfg, settings_service, db):
    import discord

    service = StatusService(db, cfg, settings_service)

    # 1. Default presence
    presence = await service.get_presence()
    assert presence["text"] == cfg.activity_text
    assert presence["type"] == cfg.activity_type
    assert presence["status"] == "online"
    assert presence["is_custom"] is False

    # 2. Activity builders
    custom_act = service.build_activity("Status alert", "custom")
    assert isinstance(custom_act, discord.CustomActivity)
    assert custom_act.name == "Status alert"

    watch_act = service.build_activity("over systems", "watching")
    assert isinstance(watch_act, discord.Activity)
    assert watch_act.type == discord.ActivityType.watching

    assert service.resolve_status("online") == discord.Status.online
    assert service.resolve_status("dnd") == discord.Status.dnd
    assert service.resolve_status("idle") == discord.Status.idle
    assert service.resolve_status("invisible") == discord.Status.invisible

    # 3. Setting custom presence
    updated = await service.set_presence(
        text="⚠️ Production Degraded - Investigating",
        type="custom",
        status="dnd",
    )
    assert updated["text"] == "⚠️ Production Degraded - Investigating"
    assert updated["type"] == "custom"
    assert updated["status"] == "dnd"
    assert updated["is_custom"] is True

    # Check persistence in meta table
    assert await db.get_meta("presence_text") == "⚠️ Production Degraded - Investigating"
    assert await db.get_meta("presence_type") == "custom"
    assert await db.get_meta("presence_status") == "dnd"

    # Check snapshot integration
    snapshot = await service.snapshot()
    assert snapshot["discord"]["presence"]["text"] == "⚠️ Production Degraded - Investigating"
    assert snapshot["discord"]["presence"]["status"] == "dnd"

    # 4. Resetting presence
    reverted = await service.set_presence(text="reset")
    assert reverted["text"] == cfg.activity_text
    assert reverted["type"] == cfg.activity_type
    assert reverted["status"] == "online"
    assert reverted["is_custom"] is False
    assert await db.get_meta("presence_text") is None

