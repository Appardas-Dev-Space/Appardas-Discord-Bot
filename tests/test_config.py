from __future__ import annotations

import pytest

from app.config import ConfigError, load_settings

LOCAL = {"DISCORD_TOKEN": "x", "BOT_OWNER_IDS": "1", "ENVIRONMENT": "development", "COOKIE_SECURE": "false"}


def test_missing_required_variables_are_reported_together():
    with pytest.raises(ConfigError) as excinfo:
        load_settings({})
    message = str(excinfo.value)
    assert "DISCORD_TOKEN" in message
    assert "BOT_OWNER_IDS" in message


def test_port_resolution_prefers_port_over_http_port():
    cfg = load_settings(
        {
            **LOCAL,
            "PORT": "9000",
            "HTTP_PORT": "8080",
        }
    )
    assert cfg.http_port == 9000


def test_invalid_port_is_rejected():
    with pytest.raises(ConfigError) as excinfo:
        load_settings({"DISCORD_TOKEN": "x", "BOT_OWNER_IDS": "1", "HTTP_PORT": "99999"})
    assert "HTTP_PORT" in str(excinfo.value)


def test_production_requires_origin_and_oauth():
    with pytest.raises(ConfigError) as excinfo:
        load_settings(
            {
                "DISCORD_TOKEN": "x",
                "BOT_OWNER_IDS": "1",
            }
        )
    assert "PUBLIC_BASE_URL" in str(excinfo.value)
    assert "DISCORD_CLIENT_ID" in str(excinfo.value)
    assert "DISCORD_CLIENT_SECRET" in str(excinfo.value)


def test_defaults_are_safe(cfg):
    assert cfg.http_host == "0.0.0.0"
    assert cfg.rate_limit_per_min == 120
    assert cfg.nudge_enabled is True
    assert cfg.github_max_watches == 5
    assert cfg.display_base_url == "http://localhost:8080"
    assert cfg.enable_message_content_intent is False


def test_message_content_intent_can_be_opted_in():
    cfg = load_settings({**LOCAL, "ENABLE_MESSAGE_CONTENT_INTENT": "true"})
    assert cfg.enable_message_content_intent is True


def test_invalid_message_content_intent_is_rejected():
    with pytest.raises(ConfigError, match="ENABLE_MESSAGE_CONTENT_INTENT"):
        load_settings({"DISCORD_TOKEN": "x", "BOT_OWNER_IDS": "1", "ENABLE_MESSAGE_CONTENT_INTENT": "maybe"})


@pytest.mark.parametrize("url", ["https://example.com/", "https://example.com/docs", "https://u:p@example.com", "//example.com", "https://example.com?token=x", "https://example.com/#x", "https://example.com\\evil", "http://example.com"])
def test_origin_is_not_a_path_or_insecure_public_host(url):
    with pytest.raises(ConfigError, match="PUBLIC_BASE_URL|COOKIE_SECURE"):
        load_settings({**LOCAL, "PUBLIC_BASE_URL": url})


def test_production_cookies_and_session_bounds():
    production = {"DISCORD_TOKEN": "x", "BOT_OWNER_IDS": "1", "PUBLIC_BASE_URL": "https://example.com", "DISCORD_CLIENT_ID": "123", "DISCORD_CLIENT_SECRET": "test-only"}
    cfg = load_settings(production)
    assert cfg.cookie_secure and cfg.session_idle_days == 30 and cfg.session_max_days == 90
    with pytest.raises(ConfigError, match="COOKIE_SECURE"):
        load_settings({**production, "COOKIE_SECURE": "false"})
    with pytest.raises(ConfigError, match="SESSION_IDLE_DAYS"):
        load_settings({**LOCAL, "SESSION_IDLE_DAYS": "91", "SESSION_MAX_DAYS": "90"})
