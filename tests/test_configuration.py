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


def test_build_bot_settings_maps_automatic_login_fields():
    config = {
        "bot": {
            "login_mode": "AUTOMATIC",
            "login_username": "  usuario.teste  ",
            "login_password": "  senha.teste  ",
            "login_username_selector": "  #username  ",
            "login_password_selector": "  #password  ",
            "login_submit_selector": "  #kc-login  ",
            "post_login_url": "  https://example.com/portal  ",
            "post_login_open_new_tab": "1",
            "close_notifications_after_login": "true",
            "notification_close_selectors": ["  button:has-text('Fechar')  ", ""],
            "selected_operator_code": " 421715 ",
        }
    }

    settings = build_bot_settings(config)

    assert settings.login_mode == "automatic"
    assert settings.login_username == "usuario.teste"
    assert settings.login_password == "senha.teste"
    assert settings.login_username_selector == "#username"
    assert settings.login_password_selector == "#password"
    assert settings.login_submit_selector == "#kc-login"
    assert settings.post_login_url == "https://example.com/portal"
    assert settings.post_login_open_new_tab is True
    assert settings.close_notifications_after_login is True
    assert settings.notification_close_selectors == ["button:has-text('Fechar')"]
    assert settings.selected_operator_code == "421715"


def test_build_bot_settings_uses_env_fallback_for_login_credentials(monkeypatch):
    monkeypatch.setenv("ORIZON_LOGIN_USERNAME", "usuario.env")
    monkeypatch.setenv("ORIZON_LOGIN_PASSWORD", "senha.env")

    config = {
        "bot": {
            "login_mode": "automatic",
            "login_username": "",
            "login_password": "",
        }
    }

    settings = build_bot_settings(config)

    assert settings.login_username == "usuario.env"
    assert settings.login_password == "senha.env"
