from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from threading import Event
from typing import Any, Callable

try:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import Page, Playwright, sync_playwright
except ModuleNotFoundError:
    PlaywrightError = Exception
    Page = Any
    Playwright = Any
    sync_playwright = None

from src.core.models import GuideGroup, ProcedureRecord


class AutomationConfigurationError(Exception):
    """Erro de configuracao da automacao."""


class StopRequested(Exception):
    """Sinaliza parada solicitada pelo usuario."""


@dataclass(frozen=True)
class BotSettings:
    portal_url: str
    headless: bool
    wait_timeout_ms: int
    navigation_steps: list[str]
    selectors: dict[str, str]
    object_resource_value: str
    grau_participacao_value: str
    error_mode: str = "tolerant"
    login_mode: str = "manual"
    login_username: str = ""
    login_password: str = ""
    login_username_selector: str = "#username"
    login_password_selector: str = "#password"
    login_submit_selector: str = "#kc-login"
    post_login_selector: str = ""
    slow_mo_ms: int = 0
    screenshot_on_error: bool = True
    screenshot_dir: Path = Path("logs/screenshots")


@dataclass(frozen=True)
class AutomationResult:
    guides_success: int = 0
    guides_error: int = 0
    procedures_success: int = 0
    procedures_error: int = 0


@dataclass
class RunCallbacks:
    log: Callable[[str], None]
    on_guide_start: Callable[[GuideGroup], None] = lambda _group: None
    on_guide_success: Callable[[GuideGroup], None] = lambda _group: None
    on_guide_error: Callable[[GuideGroup, Exception], None] = lambda _group, _exc: None
    on_procedure_success: Callable[[GuideGroup, ProcedureRecord], None] = lambda _group, _record: None
    on_procedure_error: Callable[[GuideGroup, ProcedureRecord, Exception], None] = lambda _group, _record, _exc: None


class OrizonAutomator:
    def __init__(self, settings: BotSettings):
        self.settings = settings
        self._validate_settings()
        self.settings.screenshot_dir.mkdir(parents=True, exist_ok=True)

    def run(self, groups: list[GuideGroup], stop_event: Event, callbacks: RunCallbacks) -> AutomationResult:
        if sync_playwright is None:
            raise AutomationConfigurationError(
                "Dependencia ausente: instale Playwright com 'pip install -r requirements.txt' "
                "e execute 'python -m playwright install chromium'."
            )

        counters = {
            "guides_success": 0,
            "guides_error": 0,
            "procedures_success": 0,
            "procedures_error": 0,
        }

        callbacks.log("Iniciando navegador e preparando sessao no portal ORIZON.")

        with sync_playwright() as playwright:
            browser, page = self._open_browser(playwright)
            try:
                self._safe_point(stop_event)
                self._open_portal(page, callbacks)
                self._login_if_needed(page, callbacks)
                self._navigate_to_resource_screen(page, callbacks)

                for group in groups:
                    self._safe_point(stop_event)
                    callbacks.on_guide_start(group)
                    callbacks.log(f"Iniciando guia para senha {group.senha}.")

                    try:
                        self._process_guide(page, group, stop_event, callbacks, counters)
                        counters["guides_success"] += 1
                        callbacks.on_guide_success(group)
                    except StopRequested:
                        raise
                    except Exception as exc:
                        counters["guides_error"] += 1
                        callbacks.on_guide_error(group, exc)
                        callbacks.log(f"Falha na guia da senha {group.senha}: {exc}")
                        self._capture_error_screenshot(page, f"guide_{group.senha}", callbacks)
                        if self.settings.error_mode == "strict":
                            raise
            finally:
                page.context.close()
                browser.close()

        return AutomationResult(**counters)

    def _open_browser(self, playwright: Playwright):
        browser = playwright.chromium.launch(headless=self.settings.headless, slow_mo=self.settings.slow_mo_ms)
        context = browser.new_context()
        page = context.new_page()
        return browser, page

    def _open_portal(self, page: Page, callbacks: RunCallbacks) -> None:
        callbacks.log(f"Acessando portal: {self.settings.portal_url}")
        page.goto(self.settings.portal_url, wait_until="domcontentloaded", timeout=self.settings.wait_timeout_ms)

    def _login_if_needed(self, page: Page, callbacks: RunCallbacks) -> None:
        if self.settings.login_mode == "manual":
            if not self.settings.post_login_selector:
                callbacks.log("Login manual sem seletor de confirmacao. Continuando fluxo sem validacao adicional.")
                return

            callbacks.log("Aguardando login manual no portal.")
            page.locator(self.settings.post_login_selector).first.wait_for(
                state="visible",
                timeout=self.settings.wait_timeout_ms,
            )
            callbacks.log("Login detectado com sucesso.")
            return

        if self.settings.login_mode != "automatic":
            raise AutomationConfigurationError(
                "Valor invalido para 'bot.login_mode'. Use 'manual' ou 'automatic'."
            )

        callbacks.log("Realizando login automatico.")
        self._set_value(page, self.settings.login_username_selector, self.settings.login_username)
        self._set_value(page, self.settings.login_password_selector, self.settings.login_password)

        if self.settings.login_submit_selector.strip():
            self._click(page, self.settings.login_submit_selector)
        else:
            page.locator(self.settings.login_password_selector).first.press(
                "Enter",
                timeout=self.settings.wait_timeout_ms,
            )

        if not self.settings.post_login_selector:
            callbacks.log("Login automatico enviado sem seletor de confirmacao. Continuando fluxo.")
            return

        callbacks.log("Aguardando confirmacao do login automatico.")
        page.locator(self.settings.post_login_selector).first.wait_for(
            state="visible",
            timeout=self.settings.wait_timeout_ms,
        )
        callbacks.log("Login detectado com sucesso.")

    def _navigate_to_resource_screen(self, page: Page, callbacks: RunCallbacks) -> None:
        if not self.settings.navigation_steps:
            callbacks.log("Nenhuma etapa de navegacao configurada. Assumindo tela de recurso ja aberta.")
            return

        callbacks.log("Navegando no menu de Glosas -> Recurso.")
        for selector in self.settings.navigation_steps:
            self._click(page, selector)

    def _process_guide(
        self,
        page: Page,
        group: GuideGroup,
        stop_event: Event,
        callbacks: RunCallbacks,
        counters: dict[str, int],
    ) -> None:
        self._safe_point(stop_event)
        self._click(page, self.settings.selectors["new_guide_button"])
        self._fill_guide_header(page, group.first_record)

        for record in group.records:
            self._safe_point(stop_event)
            try:
                self._add_procedure(page, record)
                counters["procedures_success"] += 1
                callbacks.on_procedure_success(group, record)
            except StopRequested:
                raise
            except Exception as exc:
                counters["procedures_error"] += 1
                callbacks.on_procedure_error(group, record, exc)
                callbacks.log(
                    f"Erro ao incluir procedimento da senha {group.senha} na linha {record.row_number}: {exc}"
                )
                self._capture_error_screenshot(
                    page,
                    f"procedure_{group.senha}_{record.row_number}",
                    callbacks,
                )
                if self.settings.error_mode == "strict":
                    raise

        self._safe_point(stop_event)
        self._click(page, self.settings.selectors["save_guide_button"])
        callbacks.log(f"Guia da senha {group.senha} finalizada.")

    def _fill_guide_header(self, page: Page, record: ProcedureRecord) -> None:
        self._set_value(page, self.settings.selectors["object_resource_field"], self.settings.object_resource_value)
        self._set_value(page, self.settings.selectors["protocol_number_field"], record.protocolo_numero)
        self._set_value(page, self.settings.selectors["provider_identifier_field"], record.prestador_numero)
        self._set_value(page, self.settings.selectors["resource_type_field"], "Recurso de Guia")
        self._set_value(page, self.settings.selectors["origin_guide_number_field"], record.guia_prestador)
        self._set_value(page, self.settings.selectors["password_field"], record.senha)
        self._set_value(page, self.settings.selectors["operator_guide_number_field"], record.numero_guia_operadora)

    def _add_procedure(self, page: Page, record: ProcedureRecord) -> None:
        self._click(page, self.settings.selectors["new_procedure_button"])
        self._set_value(page, self.settings.selectors["service_date_field"], record.data_realizacao.strftime("%d/%m/%Y"))
        self._set_value(page, self.settings.selectors["gloss_code_field"], record.tipo_glosa)
        self._set_value(page, self.settings.selectors["participation_degree_field"], self.settings.grau_participacao_value)
        self._set_value(page, self.settings.selectors["procedure_code_field"], record.codigo_procedimento)
        self._set_value(page, self.settings.selectors["procedure_description_field"], record.descricao_procedimento)
        self._set_value(page, self.settings.selectors["justification_field"], record.justificativa)
        self._set_value(page, self.settings.selectors["value_field"], _format_brl_decimal(record.valor_glosado))

        save_selector = self.settings.selectors.get("save_procedure_button")
        if save_selector:
            self._click(page, save_selector)

    def _click(self, page: Page, selector: str) -> None:
        locator = page.locator(selector).first
        locator.wait_for(state="visible", timeout=self.settings.wait_timeout_ms)
        locator.click(timeout=self.settings.wait_timeout_ms)

    def _set_value(self, page: Page, selector: str, value: str) -> None:
        locator = page.locator(selector).first
        locator.wait_for(state="visible", timeout=self.settings.wait_timeout_ms)
        tag_name = locator.evaluate("el => el.tagName.toLowerCase()")
        text_value = value.strip()

        if tag_name == "select":
            try:
                locator.select_option(label=text_value, timeout=self.settings.wait_timeout_ms)
            except PlaywrightError:
                locator.select_option(value=text_value, timeout=self.settings.wait_timeout_ms)
            return

        locator.fill("", timeout=self.settings.wait_timeout_ms)
        locator.fill(text_value, timeout=self.settings.wait_timeout_ms)

    def _safe_point(self, stop_event: Event) -> None:
        if stop_event.is_set():
            raise StopRequested("Parada solicitada pelo usuario.")

    def _capture_error_screenshot(self, page: Page, prefix: str, callbacks: RunCallbacks) -> None:
        if not self.settings.screenshot_on_error:
            return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        target = self.settings.screenshot_dir / f"{prefix}_{timestamp}.png"
        try:
            page.screenshot(path=str(target), full_page=True)
            callbacks.log(f"Screenshot salvo em: {target}")
        except Exception as exc:
            callbacks.log(f"Nao foi possivel gerar screenshot: {exc}")

    def _validate_settings(self) -> None:
        if not self.settings.object_resource_value.strip():
            raise AutomationConfigurationError(
                "Configure 'bot.object_resource_value' no arquivo config/config.json."
            )
        if not self.settings.grau_participacao_value.strip():
            raise AutomationConfigurationError(
                "Configure 'bot.grau_participacao_value' no arquivo config/config.json."
            )
        if self.settings.error_mode not in {"tolerant", "strict"}:
            raise AutomationConfigurationError("Valor invalido para 'bot.error_mode'. Use 'tolerant' ou 'strict'.")
        if self.settings.login_mode not in {"manual", "automatic"}:
            raise AutomationConfigurationError("Valor invalido para 'bot.login_mode'. Use 'manual' ou 'automatic'.")

        if self.settings.login_mode == "automatic":
            if not self.settings.login_username.strip():
                raise AutomationConfigurationError(
                    "Configure 'bot.login_username' no arquivo config/config.json "
                    "ou defina a variavel de ambiente ORIZON_LOGIN_USERNAME."
                )
            if not self.settings.login_password.strip():
                raise AutomationConfigurationError(
                    "Configure 'bot.login_password' no arquivo config/config.json "
                    "ou defina a variavel de ambiente ORIZON_LOGIN_PASSWORD."
                )
            if not self.settings.login_username_selector.strip():
                raise AutomationConfigurationError("Configure 'bot.login_username_selector' no arquivo config/config.json.")
            if not self.settings.login_password_selector.strip():
                raise AutomationConfigurationError("Configure 'bot.login_password_selector' no arquivo config/config.json.")

        required_keys = [
            "new_guide_button",
            "object_resource_field",
            "protocol_number_field",
            "provider_identifier_field",
            "resource_type_field",
            "origin_guide_number_field",
            "password_field",
            "operator_guide_number_field",
            "new_procedure_button",
            "service_date_field",
            "gloss_code_field",
            "participation_degree_field",
            "procedure_code_field",
            "procedure_description_field",
            "justification_field",
            "value_field",
            "save_guide_button",
        ]

        missing = [key for key in required_keys if not self.settings.selectors.get(key)]
        if missing:
            missing_text = ", ".join(missing)
            raise AutomationConfigurationError(f"Seletores obrigatorios ausentes na configuracao: {missing_text}")


def _format_brl_decimal(value: Decimal) -> str:
    quantized = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"{quantized:.2f}".replace(".", ",")
