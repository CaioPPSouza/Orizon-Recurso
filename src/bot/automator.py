from __future__ import annotations

import base64
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from threading import Event
from time import monotonic
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

try:
    from playwright.sync_api import Error as PlaywrightError
    from playwright.sync_api import Frame
    from playwright.sync_api import Page, Playwright, sync_playwright
except ModuleNotFoundError:
    PlaywrightError = Exception
    Frame = Any
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
    post_login_url: str = ""
    post_login_open_new_tab: bool = False
    close_notifications_after_login: bool = True
    notification_close_selectors: list[str] = field(default_factory=list)
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
            self._configure_context_dialog_handlers(page, callbacks)
            try:
                self._safe_point(stop_event)
                self._open_portal(page, callbacks)
                page = self._login_if_needed(page, callbacks)
                page = self._navigate_to_resource_screen(page, callbacks)

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

    def _configure_context_dialog_handlers(self, page: Page, callbacks: RunCallbacks) -> None:
        self._attach_dialog_handler(page, callbacks)

        def on_new_page(new_page: Page) -> None:
            self._attach_dialog_handler(new_page, callbacks)

        page.context.on("page", on_new_page)

    def _attach_dialog_handler(self, page: Page, callbacks: RunCallbacks) -> None:
        def on_dialog(dialog) -> None:
            try:
                callbacks.log(f"Dialogo JS detectado ({dialog.type}). Fechando automaticamente.")
                dialog.dismiss()
            except Exception as exc:
                callbacks.log(f"Nao foi possivel fechar dialogo JS automaticamente: {exc}")

        page.on("dialog", on_dialog)

    def _open_portal(self, page: Page, callbacks: RunCallbacks) -> None:
        callbacks.log(f"Acessando portal: {self.settings.portal_url}")
        page.goto(self.settings.portal_url, wait_until="domcontentloaded", timeout=self.settings.wait_timeout_ms)

    def _login_if_needed(self, page: Page, callbacks: RunCallbacks) -> Page:
        if self.settings.login_mode == "manual":
            if not self.settings.post_login_selector:
                callbacks.log("Login manual sem seletor de confirmacao. Continuando fluxo sem validacao adicional.")
                if self.settings.post_login_url.strip():
                    callbacks.log(
                        "Nao foi possivel abrir a URL pos-login automaticamente porque "
                        "'bot.post_login_selector' esta vazio em modo manual."
                    )
                return page

            callbacks.log("Aguardando login manual no portal.")
            page.locator(self.settings.post_login_selector).first.wait_for(
                state="visible",
                timeout=self.settings.wait_timeout_ms,
            )
            callbacks.log("Login detectado com sucesso.")
            return self._prepare_post_login_page(page, callbacks)

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

        callbacks.log("Aguardando confirmacao do login automatico.")
        login_confirmed = False

        if self.settings.post_login_selector.strip():
            try:
                page.locator(self.settings.post_login_selector).first.wait_for(
                    state="visible",
                    timeout=self.settings.wait_timeout_ms,
                )
                login_confirmed = True
            except PlaywrightError:
                callbacks.log(
                    "Seletor de pos-login nao encontrado no tempo esperado. "
                    "Tentando confirmar login por URL."
                )

        if not login_confirmed:
            self._wait_for_login_transition(page)

        callbacks.log("Login detectado com sucesso.")
        return self._prepare_post_login_page(page, callbacks)

    def _prepare_post_login_page(self, page: Page, callbacks: RunCallbacks) -> Page:
        target_page = self._open_post_login_url(page, callbacks)
        self._send_escape_after_login(target_page, callbacks)
        target_page = self._close_extra_pages(target_page, callbacks)
        self._dismiss_post_login_notifications(target_page, callbacks)
        return target_page

    def _send_escape_after_login(self, page: Page, callbacks: RunCallbacks) -> None:
        callbacks.log("Aguardando 10 segundos apos o login para dispensar janelas com ESC.")
        page.wait_for_timeout(10000)
        for _ in range(2):
            try:
                page.keyboard.press("Escape")
                page.wait_for_timeout(200)
            except PlaywrightError:
                continue
        callbacks.log("Tecla ESC enviada apos o login.")

    def _open_post_login_url(self, page: Page, callbacks: RunCallbacks) -> Page:
        target_url = self.settings.post_login_url.strip()
        if not target_url:
            return page

        if self.settings.post_login_open_new_tab:
            callbacks.log(f"Abrindo pagina pos-login em nova aba: {target_url}")
            target_page = page.context.new_page()
            target_page.goto(target_url, wait_until="domcontentloaded", timeout=self.settings.wait_timeout_ms)
            callbacks.log(f"URL apos abertura pos-login: {target_page.url}")
            return target_page

        callbacks.log(f"Abrindo pagina pos-login: {target_url}")
        page.goto(target_url, wait_until="domcontentloaded", timeout=self.settings.wait_timeout_ms)
        callbacks.log(f"URL apos abertura pos-login: {page.url}")
        return page

    def _dismiss_post_login_notifications(self, page: Page, callbacks: RunCallbacks) -> None:
        if not self.settings.close_notifications_after_login:
            return

        selectors = [selector.strip() for selector in self.settings.notification_close_selectors if selector.strip()]
        if not selectors:
            callbacks.log("Fechamento automatico de notificacoes habilitado, mas sem seletores configurados.")
            return

        page.wait_for_timeout(400)
        for _ in range(2):
            try:
                page.keyboard.press("Escape")
            except PlaywrightError:
                pass
        closed_total = 0
        rounds_without_closure = 0

        for _ in range(10):
            closed_round = 0
            frames = [frame for frame in page.frames if not frame.is_detached()]
            for frame in frames:
                closed_round += self._close_notification_controls_in_frame(frame, selectors, page)

            if closed_round == 0:
                rounds_without_closure += 1
                if rounds_without_closure >= 3:
                    break
                page.wait_for_timeout(400)
                continue

            rounds_without_closure = 0
            closed_total += closed_round
            page.wait_for_timeout(250)

        if closed_total > 0:
            callbacks.log(f"Notificacoes fechadas automaticamente: {closed_total}.")

    def _close_notification_controls_in_frame(
        self,
        frame: Frame,
        selectors: list[str],
        page: Page,
    ) -> int:
        closed = 0

        for selector in selectors:
            try:
                locator = frame.locator(selector)
                count = min(locator.count(), 5)
            except PlaywrightError:
                continue

            for index in range(count):
                target = locator.nth(index)
                try:
                    if not target.is_visible(timeout=700):
                        continue
                    try:
                        target.click(timeout=2000)
                    except PlaywrightError:
                        target.click(timeout=2000, force=True)
                    page.wait_for_timeout(250)
                    closed += 1
                except PlaywrightError:
                    continue

        return closed

    def _close_extra_pages(self, page: Page, callbacks: RunCallbacks) -> Page:
        page.wait_for_timeout(500)
        pages = [candidate for candidate in page.context.pages if not candidate.is_closed()]
        if len(pages) <= 1:
            return page

        primary_page = self._pick_primary_page(page, pages)
        closed = 0
        for candidate in pages:
            if candidate == primary_page:
                continue
            try:
                candidate.close()
                closed += 1
            except PlaywrightError:
                continue

        if closed > 0:
            callbacks.log(f"Janelas extras fechadas automaticamente: {closed}.")
        return primary_page

    def _pick_primary_page(self, fallback_page: Page, pages: list[Page]) -> Page:
        if fallback_page in pages and not fallback_page.is_closed():
            return fallback_page

        preferred_tokens = (
            "portalservicos.orizonbrasil.com.br",
            "portal.orizon.com.br",
            "/fature/",
            "prestador.html",
            "nova_pagina.html",
        )
        for candidate in pages:
            try:
                url = (candidate.url or "").lower()
            except Exception:
                continue
            if any(token in url for token in preferred_tokens):
                return candidate

        return pages[-1]

    def _wait_for_login_transition(self, page: Page) -> None:
        portal_url = self.settings.portal_url.strip().lower()
        current_url = page.url

        if "auth/realms/" in portal_url:
            page.wait_for_url(
                lambda url: "auth/realms/" not in url.lower(),
                timeout=self.settings.wait_timeout_ms,
            )
            return

        page.wait_for_url(
            lambda url: url != current_url,
            timeout=self.settings.wait_timeout_ms,
        )

    def _navigate_to_resource_screen(self, page: Page, callbacks: RunCallbacks) -> Page:
        page = self._close_extra_pages(page, callbacks)
        self._dismiss_post_login_notifications(page, callbacks)

        if not self.settings.navigation_steps:
            callbacks.log("Nenhuma etapa de navegacao configurada. Assumindo tela de recurso ja aberta.")
            return page

        callbacks.log("Navegando no menu de Glosas -> Recurso.")
        for selector in self.settings.navigation_steps:
            self._dismiss_post_login_notifications(page, callbacks)
            try:
                page = self._click_navigation_step(page, selector, callbacks)
            except PlaywrightError:
                callbacks.log(
                    f"Falha ao clicar em '{selector}'. Tentando fechar notificacoes/janelas e repetir."
                )
                page = self._close_extra_pages(page, callbacks)
                self._dismiss_post_login_notifications(page, callbacks)
                page = self._click_navigation_step(page, selector, callbacks)
            self._dismiss_post_login_notifications(page, callbacks)

        return page

    def _click_navigation_step(self, page: Page, selector: str, callbacks: RunCallbacks) -> Page:
        before_pages = [candidate for candidate in page.context.pages if not candidate.is_closed()]
        before_ids = {id(candidate) for candidate in before_pages}
        before_url = (page.url or "").strip()
        allow_new_tab_switch = self._selector_may_open_navigation_tab(selector)

        self._click(page, selector)
        target_page, opened_new_tab = self._wait_for_navigation_context_change(
            page,
            before_ids,
            before_url,
            allow_new_tab_switch=allow_new_tab_switch,
        )
        target_page = self._resolve_portal_services_intermediate_url(target_page, callbacks)

        if opened_new_tab:
            try:
                target_page.bring_to_front()
            except PlaywrightError:
                pass
            callbacks.log(f"Nova aba detectada apos clicar em '{selector}'. Continuando fluxo nessa aba.")

        return target_page

    def _wait_for_navigation_context_change(
        self,
        page: Page,
        before_ids: set[int],
        before_url: str,
        *,
        allow_new_tab_switch: bool,
    ) -> tuple[Page, bool]:
        deadline = monotonic() + min(self.settings.wait_timeout_ms, 15000) / 1000
        opened_new_tab = False

        while monotonic() < deadline:
            if allow_new_tab_switch:
                candidates = [candidate for candidate in page.context.pages if not candidate.is_closed()]
                target_page, has_new_page = self._select_preferred_navigation_page(page, candidates, before_ids)
                if has_new_page:
                    opened_new_tab = True
                    return target_page, opened_new_tab

            current_url = (page.url or "").strip()
            if current_url and current_url != before_url:
                return page, opened_new_tab

            page.wait_for_timeout(250)

        return page, opened_new_tab

    def _selector_may_open_navigation_tab(self, selector: str) -> bool:
        normalized = selector.strip().lower()
        if not normalized:
            return False

        return "linktopbarmoduloglosas" in normalized or "servi" in normalized

    def _select_preferred_navigation_page(
        self,
        fallback_page: Page,
        pages: list[Page],
        before_ids: set[int],
    ) -> tuple[Page, bool]:
        new_pages = [candidate for candidate in pages if id(candidate) not in before_ids]
        if not new_pages:
            return fallback_page, False

        preferred_tokens = (
            "portalservicos.orizonbrasil.com.br",
            "nova_pagina.html",
        )

        for token in preferred_tokens:
            for candidate in reversed(new_pages):
                try:
                    url = (candidate.url or "").lower()
                except Exception:
                    continue
                if token in url:
                    return candidate, True

        return fallback_page, False

    def _resolve_portal_services_intermediate_url(self, page: Page, callbacks: RunCallbacks) -> Page:
        try:
            current_url = (page.url or "").strip()
        except Exception:
            return page

        if "nova_pagina.html" not in current_url or "newpagina=" not in current_url:
            return page

        try:
            parsed = urlparse(current_url)
            encoded_target = parse_qs(parsed.query).get("newpagina", [""])[0]
            target_url = self._decode_base64_url(encoded_target)
        except Exception as exc:
            callbacks.log(f"Nao foi possivel interpretar URL intermediaria apos clique de navegacao: {exc}")
            return page

        if not target_url or not target_url.lower().startswith(("http://", "https://")):
            return page

        callbacks.log(f"URL intermediaria detectada. Abrindo destino final: {target_url}")
        page.goto(target_url, wait_until="domcontentloaded", timeout=self.settings.wait_timeout_ms)
        return page

    def _decode_base64_url(self, value: str) -> str:
        encoded = (value or "").strip()
        if not encoded:
            return ""

        padded = encoded + ("=" * ((4 - len(encoded) % 4) % 4))
        for decoder in (base64.b64decode, base64.urlsafe_b64decode):
            try:
                decoded = decoder(padded).decode("utf-8", errors="ignore").strip()
                if decoded:
                    return decoded
            except Exception:
                continue
        return ""

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
        frames = [frame for frame in page.frames if not frame.is_detached()]
        for frame in frames:
            if self._try_click_in_frame(frame, selector):
                return

        fallback = page.locator(selector).first
        fallback.wait_for(state="visible", timeout=self.settings.wait_timeout_ms)
        fallback.click(timeout=self.settings.wait_timeout_ms)

    def _try_click_in_frame(self, frame: Frame, selector: str) -> bool:
        locator = frame.locator(selector)
        try:
            count = min(locator.count(), 12)
        except PlaywrightError:
            return False

        for index in range(count):
            candidate = locator.nth(index)
            try:
                candidate.wait_for(state="visible", timeout=700)
                candidate.click(timeout=self.settings.wait_timeout_ms)
                return True
            except PlaywrightError:
                continue

        return False

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

        if (
            self.settings.login_mode == "manual"
            and self.settings.post_login_url.strip()
            and not self.settings.post_login_selector.strip()
        ):
            raise AutomationConfigurationError(
                "Para usar 'bot.post_login_url' com login manual, configure tambem 'bot.post_login_selector'."
            )

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
        if not isinstance(self.settings.notification_close_selectors, list):
            raise AutomationConfigurationError(
                "Valor invalido para 'bot.notification_close_selectors'. Use uma lista de seletores."
            )

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
