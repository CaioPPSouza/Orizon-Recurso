from __future__ import annotations

import pytest

from src.configuration import build_bot_settings


def test_build_bot_settings_coerces_boolean_and_numeric_fields():
    config = {
        "bot": {
            "headless": "false",
            "wait_timeout_ms": "45000",
            "slow_mo_ms": "15",
            "screenshot_on_error": "0",
            "navigation_steps": ["  text=Glosas  ", "", "text=Recurso"],
            "selectors": {
                "new_guide_button": "  role=button[name='Nova guia']  ",
            },
        }
    }

    settings = build_bot_settings(config)

    assert settings.headless is False
    assert settings.wait_timeout_ms == 45000
    assert settings.slow_mo_ms == 15
    assert settings.screenshot_on_error is False
    assert settings.navigation_steps == ["text=Glosas", "text=Recurso"]
    assert settings.selectors["new_guide_button"] == "role=button[name='Nova guia']"


def test_build_bot_settings_rejects_invalid_boolean_values():
    config = {
        "bot": {
            "headless": "talvez",
        }
    }

    with pytest.raises(ValueError) as exc:
        build_bot_settings(config)

    assert "bot.headless" in str(exc.value)


def test_build_bot_settings_rejects_invalid_wait_timeout():
    config = {
        "bot": {
            "wait_timeout_ms": "abc",
        }
    }

    with pytest.raises(ValueError) as exc:
        build_bot_settings(config)

    assert "bot.wait_timeout_ms" in str(exc.value)
