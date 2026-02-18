from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from src.bot.automator import AutomationConfigurationError, BotSettings, OrizonAutomator


def _build_settings() -> BotSettings:
    selectors = {
        "new_guide_button": "x",
        "object_resource_field": "x",
        "protocol_number_field": "x",
        "provider_identifier_field": "x",
        "resource_type_field": "x",
        "origin_guide_number_field": "x",
        "password_field": "x",
        "operator_guide_number_field": "x",
        "new_procedure_button": "x",
        "service_date_field": "x",
        "gloss_code_field": "x",
        "participation_degree_field": "x",
        "procedure_code_field": "x",
        "procedure_description_field": "x",
        "justification_field": "x",
        "value_field": "x",
        "save_procedure_button": "x",
        "save_guide_button": "x",
    }
    return BotSettings(
        portal_url="https://portal.orizon.com.br",
        headless=True,
        wait_timeout_ms=120000,
        navigation_steps=[],
        selectors=selectors,
        object_resource_value="Recurso de Guia",
        grau_participacao_value="Executante",
        screenshot_dir=Path("logs/screenshots"),
    )


def test_decode_base64_url_standard():
    automator = OrizonAutomator(_build_settings())
    encoded = "aHR0cHM6Ly9wb3J0YWxzZXJ2aWNvcy5vcml6b25icmFzaWwuY29tLmJyL2RlZmF1bHQuYXNweA=="

    decoded = automator._decode_base64_url(encoded)

    assert decoded == "https://portalservicos.orizonbrasil.com.br/default.aspx"


def test_decode_base64_url_urlsafe_without_padding():
    automator = OrizonAutomator(_build_settings())
    encoded = "aHR0cHM6Ly9wb3J0YWxzZXJ2aWNvcy5vcml6b25icmFzaWwuY29tLmJyL2RlZmF1bHQuYXNweA"

    decoded = automator._decode_base64_url(encoded)

    assert decoded == "https://portalservicos.orizonbrasil.com.br/default.aspx"


def test_decode_base64_url_invalid_returns_empty_string():
    automator = OrizonAutomator(_build_settings())

    decoded = automator._decode_base64_url("###")

    assert decoded == ""


def test_rejects_invalid_selected_operator_code():
    invalid_settings = replace(_build_settings(), selected_operator_code="9999")

    with pytest.raises(AutomationConfigurationError):
        OrizonAutomator(invalid_settings)


def test_rejects_start_from_current_page_without_existing_browser():
    invalid_settings = replace(_build_settings(), start_from_current_page=True, use_existing_browser=False)

    with pytest.raises(AutomationConfigurationError):
        OrizonAutomator(invalid_settings)


def test_rejects_invalid_execution_mode():
    invalid_settings = replace(_build_settings(), execution_mode="partial")

    with pytest.raises(AutomationConfigurationError):
        OrizonAutomator(invalid_settings)


def test_rejects_empty_resource_option_value():
    invalid_settings = replace(_build_settings(), resource_option_value="")

    with pytest.raises(AutomationConfigurationError):
        OrizonAutomator(invalid_settings)


def test_normalize_provider_identifier_removes_decimal_suffix():
    automator = OrizonAutomator(_build_settings())

    normalized = automator._normalize_provider_identifier("123456.0")

    assert normalized == "123456"


def test_normalize_provider_identifier_keeps_digits_from_formatted_text():
    automator = OrizonAutomator(_build_settings())

    normalized = automator._normalize_provider_identifier("12.345.678/0001-99")

    assert normalized == "12345678000199"


def test_build_operator_option_selectors_handles_5711_alias():
    automator = OrizonAutomator(_build_settings())

    selectors = automator._build_operator_option_selectors("5711")
    candidates = automator._build_operator_code_candidates("5711")

    assert any("005711" in selector for selector in selectors)
    assert any("5711" in selector for selector in selectors)
    assert "005711" in candidates
    assert "5711" in candidates


def test_build_operator_option_selectors_for_421715():
    automator = OrizonAutomator(_build_settings())

    selectors = automator._build_operator_option_selectors("421715")
    exact_selectors = automator._build_operator_exact_item_selectors("421715")

    assert any("421715" in selector for selector in selectors)
    assert any("LBI1" in selector for selector in exact_selectors)


def test_normalize_password_key_trims_and_uppercases():
    automator = OrizonAutomator(_build_settings())

    key = automator._normalize_password_key("  j7464c0 ")

    assert key == "J7464C0"


def test_is_guide_form_visible_accepts_structural_markers(monkeypatch):
    settings = _build_settings()
    selectors = dict(settings.selectors)
    selectors["object_resource_field"] = "#missing_object"
    selectors["protocol_number_field"] = "#missing_protocol"
    selectors["provider_identifier_field"] = "#missing_provider"
    automator = OrizonAutomator(replace(settings, selectors=selectors))

    visible_selectors = {
        "text=/recurso\\s+de\\s+glosa/i",
        "text=/protocolo\\s+de\\s+faturamento/i",
    }
    monkeypatch.setattr(automator, "_has_visible_selector", lambda _page, selector: selector in visible_selectors)

    assert automator._is_guide_form_visible(object())


def test_is_guide_form_visible_requires_two_structural_markers(monkeypatch):
    settings = _build_settings()
    selectors = dict(settings.selectors)
    selectors["object_resource_field"] = "#missing_object"
    selectors["protocol_number_field"] = "#missing_protocol"
    selectors["provider_identifier_field"] = "#missing_provider"
    automator = OrizonAutomator(replace(settings, selectors=selectors))

    visible_selectors = {"text=/recurso\\s+de\\s+glosa/i"}
    monkeypatch.setattr(automator, "_has_visible_selector", lambda _page, selector: selector in visible_selectors)

    assert not automator._is_guide_form_visible(object())
