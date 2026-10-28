from __future__ import annotations

import json
from unittest.mock import Mock

import pytest

from app.services.audit import AuditService
from app.services.settings import LockedCogError
from app.services.settings import SettingsService


async def test_default_state_is_online(settings_service):
    state = await settings_service.cog_state(1, "dev")
    assert state.enabled is True
    assert state.state == "online"
    assert state.locked is False


async def test_toggle_off_is_immediate_and_audited(settings_service, db):
    await settings_service.set_cog_state(1, "dev", False, 1, "investigating", source="bot")
    state = await settings_service.cog_state(1, "dev")
    assert state.enabled is False
    assert state.state == "offline"
    assert state.reason == "investigating"
    rows = await db.fetchall("SELECT * FROM audit_log WHERE action = 'cog.toggle'")
    assert len(rows) == 1
    assert '"enabled":false' in rows[0]["detail"]


async def test_locked_cogs_refuse(settings_service):
    for cog in ("status", "settings", "utility"):
        with pytest.raises(LockedCogError):
            await settings_service.set_cog_state(1, cog, False, 1)


async def test_toggle_back_on_works_without_restart(settings_service):
    await settings_service.set_cog_state(1, "github", False, 1)
    assert (await settings_service.cog_state(1, "github")).enabled is False
    await settings_service.set_cog_state(1, "github", True, 1)
    state = await settings_service.cog_state(1, "github")
    assert state.enabled is True
    assert state.state == "online"


async def test_held_announcements_are_counted(settings_service, db):
    await db.execute(
        "INSERT INTO announcements (guild_id, channel_id, author_id, payload, send_at, status, created_at) "
        "VALUES (1, 10, 1, '{}', '2030-01-01T00:00:00Z', 'pending', '2026-10-22T00:00:00Z')"
    )
    await settings_service.set_cog_state(1, "dev", False, 1, "maintenance", source="web")
    row = await db.fetchone("SELECT detail FROM audit_log ORDER BY id DESC LIMIT 1")
    assert '"held_announcements":1' in row["detail"]
    # Rows are held, never deleted.
    assert await db.scalar("SELECT COUNT(*) FROM announcements WHERE status = 'pending'") == 1


async def test_fault_state_overrides_online(settings_service):
    settings_service.set_fault("github", "ImportError")
    state = await settings_service.cog_state(1, "github")
    assert state.state == "faulted"
    assert await settings_service.is_enabled(1, "github") is False
    settings_service.clear_fault("github")
    assert (await settings_service.cog_state(1, "github")).state == "online"


async def test_config_set_and_get(settings_service):
    await settings_service.update_setting(1, "timezone", "Europe/Berlin", 1)
    assert await settings_service.get_setting(1, "timezone") == "Europe/Berlin"
    tz = await settings_service.timezone(1)
    assert tz.key == "Europe/Berlin"


async def test_prefix_defaults_to_mention_only(settings_service):
    assert await settings_service.command_prefix(1) is None
    assert await settings_service.get_setting(1, "prefix") is None


async def test_prefix_change_is_immediate_per_guild_and_audited(settings_service, db):
    # Warm the existing cache before writing.
    assert await settings_service.command_prefix(1) is None
    await settings_service.update_setting(1, "prefix", "!", 7)
    assert await settings_service.command_prefix(1) == "!"
    await settings_service.update_setting(1, "prefix", "??", 7)
    assert await settings_service.command_prefix(1) == "??"
    assert await settings_service.command_prefix(2) is None
    rows = await db.fetchall("SELECT actor_id, target, detail FROM audit_log WHERE action = 'config.set' ORDER BY id")
    assert len(rows) == 2
    assert rows[0]["actor_id"] == 7
    assert rows[0]["target"] == "prefix"
    assert json.loads(rows[0]["detail"]) == {"before": None, "after": "!", "source": "bot"}
    assert json.loads(rows[1]["detail"])["before"] == "!"


async def test_prefix_reset_is_immediate_and_preserves_other_settings(settings_service, db):
    await db.execute("UPDATE guild_settings SET settings = ? WHERE guild_id = 1", ('{"theme":"amber"}',))
    await settings_service.update_setting(1, "prefix", "!", 1)
    await settings_service.update_setting(1, "prefix", None, 1)
    assert await settings_service.command_prefix(1) is None
    row = await db.fetchone("SELECT settings FROM guild_settings WHERE guild_id = 1")
    assert json.loads(row["settings"]) == {"theme": "amber", "prefix": None}
    row = await db.fetchone("SELECT detail FROM audit_log ORDER BY id DESC LIMIT 1")
    assert json.loads(row["detail"])["after"] is None


async def test_prefix_survives_database_reopen_without_migration(settings_service, db, cfg):
    await settings_service.update_setting(1, "prefix", "';--", 1)
    await db.close()
    await db.connect()
    restarted = SettingsService(db, cfg, AuditService(db))
    assert await restarted.command_prefix(1) == "';--"
    assert await db.get_meta("schema_version") == "4"


@pytest.mark.parametrize("value", ["", "a b", "/", "<@123>", "<#123>", "x" * 17, 123])
async def test_invalid_prefix_never_changes_settings_or_audit(settings_service, db, value):
    await settings_service.update_setting(1, "prefix", "!", 1)
    with pytest.raises(ValueError):
        await settings_service.update_setting(1, "prefix", value, 1)
    assert await settings_service.command_prefix(1) == "!"
    assert await db.scalar("SELECT COUNT(*) FROM audit_log WHERE target = 'prefix'") == 1


@pytest.mark.parametrize("raw", ['{"prefix":""}', '{"prefix":"/"}', '{"prefix":"<#123>"}', '{"prefix":42}', "not json", "[]", "null"])
async def test_invalid_stored_prefix_falls_back_to_mentions(settings_service, db, raw):
    await db.execute("UPDATE guild_settings SET settings = ? WHERE guild_id = 1", (raw,))
    settings_service.invalidate(1)
    assert await settings_service.command_prefix(1) is None


async def test_prefix_changes_publish_existing_config_events(settings_service):
    settings_service.bus = Mock()
    await settings_service.update_setting(1, "prefix", "!", 2)
    settings_service.bus.publish.assert_called_once_with(
        {"type": "config.set", "guild_id": 1, "key": "prefix", "value": "!", "before": None, "source": "bot", "actor_id": 2}
    )
