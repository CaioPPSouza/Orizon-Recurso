from __future__ import annotations

from pathlib import Path

from src.bot.automator import BotSettings, OrizonAutomator


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
