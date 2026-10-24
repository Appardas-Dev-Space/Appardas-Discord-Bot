from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from app.services.announcements import AnnouncementLimitError, AnnouncementService
from app.util import iso, utcnow


class FakeMessage:
    jump_url = "https://discord.com/channels/1/2/3"


class FakeChannel:
    def __init__(self, fail: bool = False) -> None:
        self.sent: list[dict] = []
        self.fail = fail

    async def send(self, **kwargs):
        if self.fail:
            raise RuntimeError("channel unavailable")
        self.sent.append(kwargs)
        return FakeMessage()


class FakeClient:
    def __init__(self, channel) -> None:
        self.channel = channel

    def get_channel(self, channel_id):
        return self.channel

    async def fetch_channel(self, channel_id):
        return self.channel

    async def fetch_user(self, user_id):
        raise RuntimeError("no DM in tests")


async def _enabled(_guild_id: int, _cog: str) -> bool:
    return True


async def _disabled(_guild_id: int, _cog: str) -> bool:
    return False


async def test_dispatch_sends_due_announcements(announcements, db):
    channel = FakeChannel()
    await announcements.create_scheduled(
        guild_id=1,
        channel_id=10,
        author_id=1,
        title="Maintenance",
        body="Back at 22:00",
        ping_role_id=None,
        send_at=utcnow() - timedelta(minutes=1),
    )
    result = await announcements.dispatch(FakeClient(channel), _enabled)
    assert result.sent == 1
    assert result.failed == 0
    assert len(channel.sent) == 1
    sent_data = channel.sent[0]
    embed = sent_data["embed"]
    assert "SERVER ANNOUNCEMENT" in embed.title
    assert embed.author.name == "Admin Announcement"
    assert embed.footer.text == "Message your `admin` to learn more"
    assert "file" in sent_data
    assert sent_data["file"].filename.startswith("announcement.")
    row = await db.fetchone("SELECT status, sent_at FROM announcements")
    assert row["status"] == "sent"
    assert row["sent_at"]


async def test_disabled_cog_holds_due_announcements(announcements, db):
    channel = FakeChannel()
    await announcements.create_scheduled(
        guild_id=1,
        channel_id=10,
        author_id=1,
        title="Held",
        body="Body",
        ping_role_id=None,
        send_at=utcnow() - timedelta(minutes=1),
    )
    result = await announcements.dispatch(FakeClient(channel), _disabled)
    assert result.sent == 0
    assert result.skipped == 1
    row = await db.fetchone("SELECT status FROM announcements")
    assert row["status"] == "pending"
    assert channel.sent == []


async def test_pending_cap_is_enforced(db, audit, cfg):
    await db.execute(
        "INSERT INTO guilds (id, name, joined_at) VALUES (1, 'G', '2026-10-22T00:00:00Z') "
        "ON CONFLICT(id) DO NOTHING"
    )
    limited = AnnouncementService(db, replace(cfg, announce_max_pending=1), audit)
    for index in range(2):
        try:
            await limited.create_scheduled(
                guild_id=1,
                channel_id=10,
                author_id=1,
                title=f"Title {index}",
                body="Body",
                ping_role_id=None,
                send_at=utcnow() + timedelta(hours=1),
            )
        except AnnouncementLimitError:
            break
    else:
        pytest.fail("the pending cap did not trip")
    assert await limited.pending_count(1) == 1


async def test_cancel_marks_cancelled(announcements, db):
    announcement_id = await announcements.create_scheduled(
        guild_id=1,
        channel_id=10,
        author_id=1,
        title="Cancel me",
        body="Body",
        ping_role_id=None,
        send_at=utcnow() + timedelta(hours=1),
    )
    assert await announcements.cancel(1, announcement_id, 1) is True
    row = await db.fetchone("SELECT status FROM announcements WHERE id = ?", (announcement_id,))
    assert row["status"] == "cancelled"
    assert await announcements.cancel(1, announcement_id, 1) is False


async def test_failure_is_retried_three_times_then_marked(announcements, db, monkeypatch):
    channel = FakeChannel(fail=True)
    sent_at = iso(utcnow() - timedelta(minutes=1))
    await db.execute(
        "INSERT INTO announcements (guild_id, channel_id, author_id, payload, send_at, status, created_at) "
        "VALUES (1, 10, 1, '{\"title\":\"x\",\"body\":\"y\"}', ?, 'pending', ?)",
        (sent_at, iso()),
    )
    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr("app.services.announcements.asyncio.sleep", no_sleep)
    result = await announcements.dispatch(FakeClient(channel), _enabled)
    assert result.failed == 1
    row = await db.fetchone("SELECT status, error FROM announcements")
    assert row["status"] == "failed"
    assert "channel unavailable" in row["error"]


def test_announcement_embed_builder():
    from app.views.embeds import announcement_embed

    # Default title
    embed1 = announcement_embed("ANNOUNCEMENT", "Here is an update.")
    assert embed1.title == "SERVER ANNOUNCEMENT"
    assert embed1.description == "Here is an update."
    assert embed1.author.name == "Admin Announcement"
    assert embed1.footer.text == "Message your `admin` to learn more"

    # Custom title
    embed2 = announcement_embed(
        "Patch Notes v2.0",
        "Full changelog details.",
        author_icon_url="https://example.com/avatar.png",
        image_url="https://example.com/banner.png",
    )
    assert embed2.title == "SERVER ANNOUNCEMENT · Patch Notes v2.0"
    assert embed2.author.name == "Admin Announcement"
    assert embed2.author.icon_url == "https://example.com/avatar.png"
    assert embed2.thumbnail.url == "https://example.com/avatar.png"
    assert embed2.image.url == "https://example.com/banner.png"
    assert embed2.footer.text == "Message your `admin` to learn more"


def test_announcement_embed_with_footer_fields_and_color():
    from app.views.embeds import announcement_embed

    fields = [
        {"name": "Field 1", "value": "Val 1", "inline": True},
        {"name": "Field 2", "value": "Val 2", "inline": False},
    ]
    embed = announcement_embed(
        "Maintenance",
        "Body text",
        footer_text="Official Staff Notice",
        fields=fields,
        color="#ED4245",
    )
    assert embed.footer.text == "Official Staff Notice"
    assert len(embed.fields) == 2
    assert embed.fields[0].name == "Field 1"
    assert embed.fields[0].value == "Val 1"
    assert embed.fields[0].inline is True
    assert embed.color.value == 0xED4245


async def test_create_with_rich_options_update_and_dispatch_one(announcements, db):
    channel = FakeChannel()
    client = FakeClient(channel)

    announcement_id = await announcements.create(
        guild_id=1,
        channel_id=10,
        author_id=100,
        title="Original Title",
        body="Original Body",
        ping_role_id="@everyone",
        send_at=utcnow() + timedelta(hours=2),
        footer_text="Custom Footer",
        fields=[{"name": "Topic", "value": "Details"}],
        color="#57F287",
    )
    assert announcement_id > 0

    # Verify get
    row = await announcements.get(1, announcement_id)
    assert row is not None
    assert row["status"] == "pending"

    # Verify update
    updated = await announcements.update(
        guild_id=1,
        announcement_id=announcement_id,
        title="Updated Title",
        body="Updated Body",
        footer_text="New Footer",
    )
    assert updated is True

    # Verify dispatch_one
    dispatched = await announcements.dispatch_one(client, announcement_id)
    assert dispatched is True
    assert len(channel.sent) == 1
    sent = channel.sent[0]
    assert sent["content"] == "@everyone"
    assert "Updated Title" in sent["embed"].title
    assert sent["embed"].footer.text == "New Footer"

    # Check status changed to sent
    row = await announcements.get(1, announcement_id)
    assert row["status"] == "sent"

    # Verify list_recent contains this announcement
    recent = await announcements.list_recent(1)
    assert any(r["id"] == announcement_id for r in recent)


