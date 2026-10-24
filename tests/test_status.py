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
