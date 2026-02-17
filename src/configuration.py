from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from src.bot import BotSettings


DEFAULT_CONFIG: dict[str, Any] = {
    "spreadsheet": {
        "sheet_name": "Planilha Analisada",
    },
    "bot": {
        "portal_url": "https://portal.orizon.com.br",
        "headless": False,
        "wait_timeout_ms": 120000,
        "slow_mo_ms": 0,
        "login_mode": "manual",
        "post_login_selector": "",
        "error_mode": "tolerant",
        "screenshot_on_error": True,
        "screenshot_dir": "logs/screenshots",
        "object_resource_value": "",
        "grau_participacao_value": "",
        "navigation_steps": [],
        "selectors": {
            "new_guide_button": "",
            "object_resource_field": "",
            "protocol_number_field": "",
            "provider_identifier_field": "",
            "resource_type_field": "",
            "origin_guide_number_field": "",
            "password_field": "",
            "operator_guide_number_field": "",
            "new_procedure_button": "",
            "service_date_field": "",
            "gloss_code_field": "",
            "participation_degree_field": "",
            "procedure_code_field": "",
            "procedure_description_field": "",
            "justification_field": "",
            "value_field": "",
            "save_procedure_button": "",
            "save_guide_button": "",
        },
    },
}


def load_runtime_config(config_path: str | Path = Path("config/config.json")) -> dict[str, Any]:
    target = Path(config_path)
    if not target.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(DEFAULT_CONFIG, indent=2, ensure_ascii=False), encoding="utf-8")

    loaded = json.loads(target.read_text(encoding="utf-8"))
    return _deep_merge(deepcopy(DEFAULT_CONFIG), loaded)


def build_bot_settings(config: dict[str, Any]) -> BotSettings:
    bot = config.get("bot", {})
    defaults = DEFAULT_CONFIG["bot"]
    return BotSettings(
        portal_url=_coerce_string(
            bot.get("portal_url", defaults["portal_url"]),
            default=str(defaults["portal_url"]),
        ),
        headless=_coerce_bool(bot.get("headless", defaults["headless"]), "bot.headless"),
        wait_timeout_ms=_coerce_int(
            bot.get("wait_timeout_ms", defaults["wait_timeout_ms"]),
            "bot.wait_timeout_ms",
            minimum=1,
        ),
        navigation_steps=_coerce_string_list(
            bot.get("navigation_steps", defaults["navigation_steps"]),
            "bot.navigation_steps",
        ),
        selectors=_coerce_string_dict(
            bot.get("selectors", defaults["selectors"]),
            default_keys=defaults["selectors"],
            field_name="bot.selectors",
        ),
        object_resource_value=_coerce_optional_string(bot.get("object_resource_value", ""), default=""),
        grau_participacao_value=_coerce_optional_string(bot.get("grau_participacao_value", ""), default=""),
        error_mode=_coerce_optional_string(bot.get("error_mode", "tolerant"), default="tolerant"),
        login_mode=_coerce_optional_string(bot.get("login_mode", "manual"), default="manual"),
        post_login_selector=_coerce_optional_string(bot.get("post_login_selector", ""), default=""),
        slow_mo_ms=_coerce_int(
            bot.get("slow_mo_ms", defaults["slow_mo_ms"]),
            "bot.slow_mo_ms",
            minimum=0,
        ),
        screenshot_on_error=_coerce_bool(
            bot.get("screenshot_on_error", defaults["screenshot_on_error"]),
            "bot.screenshot_on_error",
        ),
        screenshot_dir=Path(_coerce_string(bot.get("screenshot_dir", "logs/screenshots"), default="logs/screenshots")),
    )


def get_sheet_name(config: dict[str, Any]) -> str:
    return _coerce_string(
        config.get("spreadsheet", {}).get("sheet_name", DEFAULT_CONFIG["spreadsheet"]["sheet_name"]),
        default=str(DEFAULT_CONFIG["spreadsheet"]["sheet_name"]),
    )


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    for key, value in override.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            base[key] = _deep_merge(base[key], value)
        else:
            base[key] = value
    return base


def _coerce_bool(value: Any, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "y", "sim", "on"}:
            return True
        if normalized in {"0", "false", "no", "n", "nao", "não", "off"}:
            return False

    raise ValueError(
        f"Valor invalido para '{field_name}': {value!r}. Use true/false."
    )


def _coerce_int(value: Any, field_name: str, minimum: int | None = None) -> int:
    if isinstance(value, bool):
        raise ValueError(f"Valor invalido para '{field_name}': {value!r}.")

    parsed: int
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, float):
        if not value.is_integer():
            raise ValueError(f"Valor invalido para '{field_name}': {value!r}.")
        parsed = int(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError(f"Valor invalido para '{field_name}': {value!r}.")
        try:
            parsed = int(text)
        except ValueError as exc:
            raise ValueError(f"Valor invalido para '{field_name}': {value!r}.") from exc
    else:
        raise ValueError(f"Valor invalido para '{field_name}': {value!r}.")

    if minimum is not None and parsed < minimum:
        raise ValueError(
            f"Valor invalido para '{field_name}': {parsed}. Deve ser >= {minimum}."
        )

    return parsed


def _coerce_string(value: Any, default: str) -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text or default


def _coerce_optional_string(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


def _coerce_string_list(value: Any, field_name: str) -> list[str]:
    if not isinstance(value, list):
        raise ValueError(
            f"Valor invalido para '{field_name}': esperado lista, recebido {type(value).__name__}."
        )
    return [str(item).strip() for item in value if str(item).strip()]


def _coerce_string_dict(
    value: Any,
    default_keys: dict[str, str],
    field_name: str,
) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError(
            f"Valor invalido para '{field_name}': esperado objeto, recebido {type(value).__name__}."
        )

    merged = {key: str(default).strip() for key, default in default_keys.items()}
    for key, raw_value in value.items():
        merged[str(key)] = str(raw_value).strip() if raw_value is not None else ""

    return merged
