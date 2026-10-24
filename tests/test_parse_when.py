from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from app.services.announcements import AnnouncementError, parse_when

NOW = datetime(2026, 10, 22, 8, 0, tzinfo=timezone.utc)  # Thursday
UTC = ZoneInfo("UTC")


def test_relative_hours():
    assert parse_when("in 2h", UTC, NOW) == datetime(2026, 10, 22, 10, 0, tzinfo=timezone.utc)


def test_relative_minutes_and_days():
    assert parse_when("in 30m", UTC, NOW).hour == 8
    assert parse_when("in 30m", UTC, NOW).minute == 30
    assert parse_when("in 3d", UTC, NOW).day == 25


def test_tomorrow_defaults_to_nine():
    result = parse_when("tomorrow", UTC, NOW)
    assert result == datetime(2026, 10, 23, 9, 0, tzinfo=timezone.utc)


def test_tomorrow_with_clock():
    result = parse_when("tomorrow 18:30", UTC, NOW)
    assert result == datetime(2026, 10, 23, 18, 30, tzinfo=timezone.utc)


def test_weekday_rolls_to_next_week_when_passed():
    # Thursday 08:00; Monday 09:00 is in the future (26 Oct).
    result = parse_when("mon 09:00", UTC, NOW)
    assert result == datetime(2026, 10, 26, 9, 0, tzinfo=timezone.utc)


def test_absolute_date():
    result = parse_when("2026-11-01 18:00", UTC, NOW)
    assert result == datetime(2026, 11, 1, 18, 0, tzinfo=timezone.utc)


def test_ampm_clock():
    result = parse_when("tomorrow 6pm", UTC, NOW)
    assert result.hour == 18


def test_timezone_conversion():
    berlin = ZoneInfo("Europe/Berlin")
    # 09:00 Berlin on 23 Oct (CEST, UTC+2) is 07:00 UTC.
    result = parse_when("tomorrow 09:00", berlin, NOW)
    assert result == datetime(2026, 10, 23, 7, 0, tzinfo=timezone.utc)


def test_today_in_the_past_is_refused():
    with pytest.raises(AnnouncementError):
        parse_when("today 07:00", UTC, NOW)


def test_unreadable_time_is_refused():
    with pytest.raises(AnnouncementError):
        parse_when("whenever", UTC, NOW)


def test_impossible_calendar_date_is_refused_as_an_announcement_error():
    with pytest.raises(AnnouncementError):
        parse_when("2026-13-01 18:00", UTC, NOW)
