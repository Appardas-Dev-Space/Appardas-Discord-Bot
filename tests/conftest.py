from __future__ import annotations

import pytest

from app.config import load_settings
from app.db import Database
from app.services.announcements import AnnouncementService
from app.services.audit import AuditService
from app.services.nudge import NudgeService
from app.services.settings import SettingsService


@pytest.fixture
def cfg(tmp_path):
    return load_settings(
        {
            "DISCORD_TOKEN": "test-token",
            "BOT_OWNER_IDS": "1",
            "DATABASE_PATH": str(tmp_path / "data" / "test.db"),
            "LOG_DIR": str(tmp_path / "logs"),
            "ENVIRONMENT": "development",
            "COOKIE_SECURE": "false",
            "PUBLIC_BASE_URL": "http://localhost:8080",
            "GITHUB_POLL_INTERVAL_S": "120",
            "LOG_LEVEL": "WARNING",
        }
    )


@pytest.fixture
async def db(cfg):
    database = Database(cfg.database_path)
    await database.connect(owner_ids=cfg.owner_ids)
    yield database
    await database.close()


@pytest.fixture
async def audit(db):
    return AuditService(db)


@pytest.fixture
async def settings_service(db, cfg, audit):
    service = SettingsService(db, cfg, audit)
    await service.ensure_guild(1, "Test Guild")
    return service


@pytest.fixture
async def announcements(db, cfg, audit):
    await db.execute(
        "INSERT INTO guilds (id, name, joined_at) VALUES (1, 'Test Guild', '2026-10-22T00:00:00Z') "
        "ON CONFLICT(id) DO NOTHING"
    )
    return AnnouncementService(db, cfg, audit)


@pytest.fixture
async def nudges(db, cfg, audit):
    return NudgeService(db, cfg, audit)
