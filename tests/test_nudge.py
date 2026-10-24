from __future__ import annotations

from types import SimpleNamespace

import discord
import pytest

from app.services.nudge import NudgeError, NudgeService


class FakeGuild:
    id = 1
    name = "Test Guild"


class FakeTarget:
    def __init__(self, user_id=2, fail: bool = False) -> None:
        self.id = user_id
        self.bot = False
        self.display_name = "Target"
        self.guild = FakeGuild()
        self.fail = fail
        self.messages: list[dict] = []

    async def send(self, **kwargs):
        if self.fail:
            response = SimpleNamespace(status=403, reason="Forbidden")
            raise discord.Forbidden(response, "Cannot send messages to this user")
        self.messages.append(kwargs)


class FakeAuthor:
    id = 1
    bot = False
    display_name = "Author"


async def test_delivered_nudge_logs_and_returns(nudges, db):
    service = nudges
    target = FakeTarget()
    result = await service.send(guild=FakeGuild(), author=FakeAuthor(), target=target, message="review #43")
    assert result.delivered is True
    assert len(target.messages) == 1
    row = await db.fetchone("SELECT delivered, message FROM nudges")
    assert row["delivered"] == 1
    assert row["message"] == "review #43"


async def test_pair_cooldown(nudges):
    service = nudges
    target = FakeTarget()
    await service.send(guild=FakeGuild(), author=FakeAuthor(), target=target, message="one")
    with pytest.raises(NudgeError) as excinfo:
        await service.send(guild=FakeGuild(), author=FakeAuthor(), target=target, message="two")
    assert "already nudged" in str(excinfo.value)


async def test_optout_blocks(nudges):
    service = nudges
    target = FakeTarget()
    await service.opt_out(target.id)
    with pytest.raises(NudgeError) as excinfo:
        await service.send(guild=FakeGuild(), author=FakeAuthor(), target=target, message="hi")
    assert "opted out" in str(excinfo.value)
    await service.opt_in(target.id)
    assert not await service.is_opted_out(target.id)


async def test_closed_dms_are_not_an_error(nudges, db):
    service = nudges
    target = FakeTarget(fail=True)
    result = await service.send(guild=FakeGuild(), author=FakeAuthor(), target=target, message="hi")
    assert result.delivered is False
    assert "direct messages are closed" in result.detail
    row = await db.fetchone("SELECT delivered FROM nudges")
    assert row["delivered"] == 0


async def test_self_and_bot_are_refused(nudges):
    service = nudges
    with pytest.raises(NudgeError):
        await service.send(guild=FakeGuild(), author=FakeAuthor(), target=FakeAuthor(), message="hi")
    bot = FakeTarget()
    bot.bot = True
    with pytest.raises(NudgeError):
        await service.send(guild=FakeGuild(), author=FakeAuthor(), target=bot, message="hi")


async def test_hourly_cap(nudges):
    service = nudges
    targets = [FakeTarget(user_id=10 + index) for index in range(6)]
    for target in targets[:5]:
        await service.send(guild=FakeGuild(), author=FakeAuthor(), target=target, message="hi")
    with pytest.raises(NudgeError) as excinfo:
        await service.send(guild=FakeGuild(), author=FakeAuthor(), target=targets[5], message="hi")
    assert "this hour" in str(excinfo.value)
