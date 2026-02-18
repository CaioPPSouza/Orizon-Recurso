from __future__ import annotations

import base64
import re
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


class GuideIncompleteError(Exception):
    """Sinaliza que uma guia nao pode ser finalizada por falta de procedimentos."""


@dataclass(frozen=True)
class BotSettings:
    portal_url: str
    headless: bool
    wait_timeout_ms: int
    navigation_steps: list[str]
    selectors: dict[str, str]
    object_resource_value: str
    grau_participacao_value: str
    resource_option_value: str = "Itens Guia"
    selected_operator_code: str = ""
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
    use_existing_browser: bool = False
    existing_browser_cdp_url: str = "http://127.0.0.1:9222"
    start_from_current_page: bool = False
    execution_mode: str = "full"
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
        if self.settings.selected_operator_code:
            callbacks.log(f"Operadora selecionada para esta execucao: {self.settings.selected_operator_code}.")

        with sync_playwright() as playwright:
            browser, page, should_close_context, should_close_browser = self._open_browser(playwright, callbacks)
            self._configure_context_dialog_handlers(page, callbacks)
            try:
                self._safe_point(stop_event)
                if self.settings.start_from_current_page:
                    callbacks.log(
                        "Modo de pagina atual habilitado. Pulando abertura do portal, login e navegacao."
                    )
                else:
                    self._open_portal(page, callbacks)
                    page = self._login_if_needed(page, callbacks)
                    page = self._navigate_to_resource_screen(page, callbacks)

                if self.settings.execution_mode == "header_protocol_only":
                    self._run_header_protocol_only(page, groups, stop_event, callbacks, counters)
                else:
                    completed_passwords: set[str] = set()
                    for guide_index, group in enumerate(groups):
                        group_key = self._normalize_password_key(group.senha)
                        if group_key in completed_passwords:
                            callbacks.log(
                                f"Senha {group.senha} ja processada com sucesso nesta sessao. Ignorando para evitar duplicidade."
                            )
                            continue
                        self._safe_point(stop_event)
                        callbacks.on_guide_start(group)
                        callbacks.log(f"Iniciando guia para senha {group.senha}.")

                        try:
                            self._process_guide(
                                page,
                                group,
                                stop_event,
                                callbacks,
                                counters,
                                guide_index=guide_index,
                            )
                            counters["guides_success"] += 1
                            completed_passwords.add(group_key)
                            callbacks.on_guide_success(group)
                        except StopRequested:
                            raise
                        except Exception as exc:
                            counters["guides_error"] += 1
                            callbacks.on_guide_error(group, exc)
                            callbacks.log(f"Falha na guia da senha {group.senha}: {exc}")
                            self._capture_error_screenshot(page, f"guide_{group.senha}", callbacks)
                            if isinstance(exc, GuideIncompleteError):
                                raise
                            if self.settings.error_mode == "strict":
                                raise
            finally:
                if should_close_context:
                    try:
                        page.context.close()
                    except Exception:
                        pass
                if should_close_browser:
                    try:
                        browser.close()
                    except Exception:
                        pass

        return AutomationResult(**counters)

    def _open_browser(self, playwright: Playwright, callbacks: RunCallbacks):
        if self.settings.use_existing_browser:
            callbacks.log(
                f"Conectando ao Chrome existente via CDP: {self.settings.existing_browser_cdp_url}"
            )
            browser = playwright.chromium.connect_over_cdp(
                self.settings.existing_browser_cdp_url,
                timeout=self.settings.wait_timeout_ms,
            )
            contexts = browser.contexts
            if not contexts:
                raise AutomationConfigurationError(
                    "Conexao CDP estabelecida, mas nenhuma aba/contexto ativo foi encontrado no Chrome."
                )
            context = contexts[-1]
            page = context.pages[-1] if context.pages else context.new_page()
            callbacks.log("Conexao com navegador existente concluida.")
            return browser, page, False, False

        browser = playwright.chromium.launch(headless=self.settings.headless, slow_mo=self.settings.slow_mo_ms)
        context = browser.new_context()
        page = context.new_page()
        return browser, page, True, True

    def _configure_context_dialog_handlers(self, page: Page, callbacks: RunCallbacks) -> None:
        self._attach_dialog_handler(page, callbacks)

        def on_new_page(new_page: Page) -> None:
            self._attach_dialog_handler(new_page, callbacks)

        page.context.on("page", on_new_page)

    def _attach_dialog_handler(self, page: Page, callbacks: RunCallbacks) -> None:
        def on_dialog(dialog) -> None:
            try:
                callbacks.log(f"Dialogo JS detectado ({dialog.type}). Confirmando automaticamente.")
                if dialog.type == "prompt":
                    dialog.accept("")
                else:
                    dialog.accept()
            except Exception as exc:
                callbacks.log(f"Nao foi possivel confirmar dialogo JS automaticamente: {exc}")

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
        *,
        guide_index: int,
    ) -> None:
        self._safe_point(stop_event)
        should_open_new_guide = True
        if self.settings.start_from_current_page and guide_index == 0 and self._is_guide_form_visible(page):
            should_open_new_guide = False
            callbacks.log("Usando a guia ja aberta na pagina atual para iniciar a primeira senha.")

        if should_open_new_guide:
            self._open_new_guide(page)
            self._select_operator_and_create_guide(page, callbacks)
        self._fill_guide_header(page, group.first_record)

        failed_procedures: list[tuple[int, Exception]] = []
        for record in group.records:
            self._safe_point(stop_event)
            try:
                self._add_procedure_with_retry(page, record)
                counters["procedures_success"] += 1
                callbacks.on_procedure_success(group, record)
            except StopRequested:
                raise
            except Exception as exc:
                counters["procedures_error"] += 1
                failed_procedures.append((record.row_number, exc))
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

        if failed_procedures:
            lines = ", ".join(str(row) for row, _ in failed_procedures)
            raise GuideIncompleteError(
                "Guia incompleta: nem todos os procedimentos foram incluidos com sucesso. "
                f"Linha(s) com falha: {lines}."
            )

        self._safe_point(stop_event)
        self._confirm_any_popup(page)
        self._wait_for_cp_principal_idle(page, timeout_ms=12000)
        self._click(page, self.settings.selectors["save_guide_button"])
        self._confirm_any_popup(page)
        self._wait_for_cp_principal_idle(page, timeout_ms=8000)
        callbacks.log(f"Guia da senha {group.senha} finalizada.")

    def _add_procedure_with_retry(self, page: Page, record: ProcedureRecord, max_attempts: int = 3) -> None:
        attempts = max(1, max_attempts)
        last_error: Exception | None = None

        for _ in range(attempts):
            self._wait_processing_overlay_to_clear(page, timeout_ms=12000)
            self._wait_for_cp_principal_idle(page, timeout_ms=12000)
            try:
                self._add_procedure(page, record)
                return
            except StopRequested:
                raise
            except Exception as exc:
                last_error = exc
                self._confirm_any_popup(page)
                self._wait_for_cp_principal_idle(page, timeout_ms=12000)
                self._wait_processing_overlay_to_clear(page, timeout_ms=12000)
                page.wait_for_timeout(600)

        if last_error is not None:
            raise last_error

    def _open_new_guide(self, page: Page) -> None:
        self._confirm_any_popup(page)
        self._wait_processing_overlay_to_clear(page, timeout_ms=10000)
        self._wait_for_cp_principal_idle(page, timeout_ms=10000)

        if self._open_new_blank_guide_from_toolbar(page):
            return

        selectors = [
            self.settings.selectors["new_guide_button"],
            "#ctl00_ContentBody_cpPrincipal_btnCriarNovaGuia_B",
            "#ctl00_ContentBody_cpPrincipal_btnCriarNovaGuia_CD",
            "#ctl00_ContentBody_cpPrincipal_btnCriarNovaGuia",
            "#ctl00_ContentBody_ASPxCallbackPanel1_lnkNovaGuiaOnly",
        ]
        if self._click_first_visible(page, selectors, timeout_ms=10000):
            self._click_new_blank_guide_option(page)
            self._wait_processing_overlay_to_clear(page, timeout_ms=10000)
            self._wait_for_cp_principal_idle(page, timeout_ms=10000)
            return
        self._click(page, self.settings.selectors["new_guide_button"])
        self._click_new_blank_guide_option(page)
        self._wait_processing_overlay_to_clear(page, timeout_ms=10000)
        self._wait_for_cp_principal_idle(page, timeout_ms=10000)

    def _open_new_blank_guide_from_toolbar(self, page: Page) -> bool:
        toolbar_plus_selectors = [
            "#ctl00_ContentBody_cpPrincipal_btnNovaGuia_B",
            "#ctl00_ContentBody_cpPrincipal_btnNovaGuia_CD",
            "#ctl00_ContentBody_cpPrincipal_tbBotoes_btnNovaGuia_B",
            "#ctl00_ContentBody_cpPrincipal_tbBotoes_btnNovaGuia_CD",
            "#ctl00_ContentBody_cpPrincipal_tbBotoes_btnNovo_B",
            "#ctl00_ContentBody_cpPrincipal_tbBotoes_btnNovo_CD",
            "#ctl00_ContentBody_cpPrincipal_btnNovo_B",
            "#ctl00_ContentBody_cpPrincipal_btnNovo_CD",
        ]
        clicked = self._click_first_visible(page, toolbar_plus_selectors, timeout_ms=3500)
        if not clicked:
            clicked = self._click_toolbar_plus_by_script(page)

        if not clicked:
            return False

        self._click_new_blank_guide_option(page)
        self._wait_processing_overlay_to_clear(page, timeout_ms=12000)
        self._wait_for_cp_principal_idle(page, timeout_ms=12000)
        return True

    def _click_toolbar_plus_by_script(self, page: Page) -> bool:
        try:
            return bool(
                page.evaluate(
                    """() => {
                        const normalize = (value) => String(value || "").toLowerCase();
                        const candidates = Array.from(document.querySelectorAll("a,button,span,div"));
                        const plusPattern = /btn(nova)?guia|novaguia|tbbotoes/i;
                        for (const node of candidates) {
                            const id = normalize(node.id);
                            const title = normalize(node.getAttribute("title"));
                            const text = normalize(node.textContent);
                            if (!id && !title && !text) {
                                continue;
                            }
                            if (id.includes("cpprocedimentos")) {
                                continue;
                            }
                            const mentionsGuide = id.includes("guia") || title.includes("nova guia") || text.includes("nova guia");
                            if (
                                (plusPattern.test(id) && mentionsGuide) ||
                                title.includes("nova guia") ||
                                text.includes("nova guia em branco")
                            ) {
                                const rect = node.getBoundingClientRect();
                                if (rect.width === 0 || rect.height === 0) {
                                    continue;
                                }
                                node.click();
                                return true;
                            }
                        }
                        return false;
                    }"""
                )
            )
        except PlaywrightError:
            return False

    def _click_new_blank_guide_option(self, page: Page) -> None:
        new_blank_selectors = [
            "button:has-text('Nova Guia em Branco')",
            "a:has-text('Nova Guia em Branco')",
            "[role='button']:has-text('Nova Guia em Branco')",
            "text='Nova Guia em Branco'",
        ]
        if self._click_first_visible(page, new_blank_selectors, timeout_ms=3500):
            return

        try:
            clicked = bool(
                page.evaluate(
                    """() => {
                        const nodes = Array.from(document.querySelectorAll("a,button,span,div"));
                        for (const node of nodes) {
                            const text = String(node.textContent || "").trim().toLowerCase();
                            if (text.includes("nova guia em branco")) {
                                const rect = node.getBoundingClientRect();
                                if (rect.width === 0 || rect.height === 0) {
                                    continue;
                                }
                                node.click();
                                return true;
                            }
                        }
                        return false;
                    }"""
                )
            )
        except PlaywrightError:
            clicked = False

        if clicked:
            return

    def _run_header_protocol_only(
        self,
        page: Page,
        groups: list[GuideGroup],
        stop_event: Event,
        callbacks: RunCallbacks,
        counters: dict[str, int],
    ) -> None:
        if not groups:
            callbacks.log("Nenhuma guia encontrada na planilha. Nada a preencher.")
            return

        group = groups[0]
        record = group.first_record

        self._safe_point(stop_event)
        callbacks.on_guide_start(group)
        callbacks.log(
            "Modo parcial ativo: preenchendo o cabecalho da guia com "
            f"a linha {record.row_number}."
        )

        try:
            self._fill_guide_header(page, record)
            callbacks.log("Cabecalho preenchido. Guia mantida em edicao para as proximas etapas.")
        except StopRequested:
            raise
        except Exception as exc:
            counters["guides_error"] += 1
            callbacks.on_guide_error(group, exc)
            callbacks.log(f"Falha no preenchimento parcial: {exc}")
            self._capture_error_screenshot(page, f"partial_{group.senha}", callbacks)
            if self.settings.error_mode == "strict":
                raise

    def _fill_guide_header(self, page: Page, record: ProcedureRecord) -> None:
        self._set_object_resource_value(page, self.settings.object_resource_value)
        self._confirm_any_popup(page)
        page.wait_for_timeout(2000)
        self._set_value_with_fallbacks(
            page,
            [
                self.settings.selectors["protocol_number_field"],
                "#ctl00_ContentBody_cpPrincipal_txtProtocolo_I",
                "#ctl00_ContentBody_cpPrincipal_txtProtocolo",
                "#ctl00_ContentBody_ucRecursoGlosa_txtNrProtocoloFaturamento_I",
                "[id*='txtProtocolo'][id$='_I']",
                "[id*='Protocolo'][id$='_I']",
                "xpath=//*[contains(normalize-space(.), 'Protocolo de Faturamento')]/following::input[1]",
            ],
            record.protocolo_numero,
            field_name="N do Protocolo de Faturamento",
        )
        page.wait_for_timeout(2000)
        provider_selectors = [
            self.settings.selectors["provider_identifier_field"],
            "#ctl00_ContentBody_cpPrincipal_txtValorIdentificacaoPrestador_I",
            "#ctl00_ContentBody_ucRecursoGlosa_txtIdentifPrestador_I",
            "[id*='Identif'][id*='Prestador'][id$='_I']",
            "xpath=//*[contains(normalize-space(.), 'Identif.') and contains(normalize-space(.), 'Prestador')]/following::input[1]",
        ]
        normalized_provider = self._normalize_provider_identifier(record.prestador_numero)
        self._set_provider_identifier_value(page, provider_selectors, normalized_provider)
        self._set_resource_option_value(page, self.settings.resource_option_value)
        self._confirm_any_popup(page)
        page.wait_for_timeout(2000)
        if not self._is_resource_option_selected(page, self.settings.resource_option_value):
            self._set_resource_option_value(page, self.settings.resource_option_value)
            self._confirm_any_popup(page)
            page.wait_for_timeout(2000)
        if not self._input_contains_expected_value(page, provider_selectors, normalized_provider):
            self._set_provider_identifier_value(page, provider_selectors, normalized_provider)
        self._set_value_with_fallbacks(
            page,
            [
                self.settings.selectors["origin_guide_number_field"],
                "#ctl00_ContentBody_cpPrincipal_txtGuiaOrigem_I",
                "[id*='txtGuiaOrigem'][id$='_I']",
                "[id*='GuiaOrigem'][id$='_I']",
                "xpath=//*[contains(normalize-space(.), 'Guia Origem')]/following::input[1]",
            ],
            record.guia_prestador,
            field_name="N da Guia Origem",
        )
        self._set_value_with_fallbacks(
            page,
            [
                self.settings.selectors["password_field"],
                "#ctl00_ContentBody_cpPrincipal_txtSenha_I",
                "[id*='txtSenha'][id$='_I']",
                "xpath=//*[contains(normalize-space(.), 'Senha')]/following::input[1]",
            ],
            record.senha,
            field_name="Senha",
        )
        self._set_value_with_fallbacks(
            page,
            [
                self.settings.selectors["operator_guide_number_field"],
                "#ctl00_ContentBody_cpPrincipal_txtGuiaOperadora_I",
                "[id*='txtGuiaOperadora'][id$='_I']",
                "[id*='NumeroGuiaOperadora'][id$='_I']",
                "xpath=//*[contains(normalize-space(.), 'Guia na Operadora')]/following::input[1]",
            ],
            record.numero_guia_operadora,
            field_name="N da Guia na Operadora",
        )
        self._wait_for_cp_principal_idle(page, timeout_ms=7000)
        if not self._is_resource_option_selected(page, self.settings.resource_option_value):
            self._set_resource_option_value(page, self.settings.resource_option_value)
            self._confirm_any_popup(page)
            self._wait_for_cp_principal_idle(page, timeout_ms=7000)
            page.wait_for_timeout(500)
        if not self._input_contains_expected_value(page, provider_selectors, normalized_provider):
            self._set_provider_identifier_value(page, provider_selectors, normalized_provider)

    def _set_object_resource_value(self, page: Page, value: str) -> None:
        target_text = value.strip()
        if not target_text:
            raise PlaywrightError("Valor de 'Objeto do Recurso' vazio.")

        input_selectors = [
            self.settings.selectors.get("object_resource_field", "").strip(),
            "#ctl00_ContentBody_cpPrincipal_ddlObjetoRecurso_I",
            "#ctl00_ContentBody_ucRecursoGlosa_cbObjetoRecurso_I",
            "#ctl00_ContentBody_ucRecursoGlosa_cmbObjetoRecurso_I",
            "[id*='ddlObjetoRecurso'][id$='_I']",
            "[id*='ObjetoRecurso'][id$='_I']",
        ]
        combo_keys = [
            "ddlObjetoRecurso",
            "ctl00_ContentBody_cpPrincipal_ddlObjetoRecurso",
            "ctl00_ContentBody_ucRecursoGlosa_cbObjetoRecurso",
        ]

        selected_with_client_api = self._select_combo_option_with_client_api(page, combo_keys, target_text)
        if not selected_with_client_api:
            dropdown_selectors = [
                "#ctl00_ContentBody_cpPrincipal_ddlObjetoRecurso_B-1",
                "#ctl00_ContentBody_cpPrincipal_ddlObjetoRecurso_I",
                "#ctl00_ContentBody_cpPrincipal_ddlObjetoRecurso",
                "#ctl00_ContentBody_ucRecursoGlosa_cbObjetoRecurso_B-1",
                "#ctl00_ContentBody_ucRecursoGlosa_cbObjetoRecurso_I",
                "#ctl00_ContentBody_ucRecursoGlosa_cbObjetoRecurso",
            ]
            option_selectors = [
                f"#ctl00_ContentBody_cpPrincipal_ddlObjetoRecurso_DDD_L_D td:has-text('{target_text}')",
                f"#ctl00_ContentBody_cpPrincipal_ddlObjetoRecurso_DDD_L_D *:has-text('{target_text}')",
                f"#ctl00_ContentBody_ucRecursoGlosa_cbObjetoRecurso_DDD_L_D td:has-text('{target_text}')",
                f"#ctl00_ContentBody_ucRecursoGlosa_cbObjetoRecurso_DDD_L_D *:has-text('{target_text}')",
                f"text='{target_text}'",
            ]

            if self._click_first_visible(page, dropdown_selectors, timeout_ms=3500):
                self._click_first_visible(page, option_selectors, timeout_ms=3500)

        if not self._is_combo_option_selected(page, input_selectors, combo_keys, target_text):
            # Ultimo fallback: tenta setar valor direto no input.
            self._set_value_with_fallbacks(
                page,
                input_selectors,
                target_text,
                field_name="Objeto do Recurso",
            )

        if not self._is_combo_option_selected(page, input_selectors, combo_keys, target_text):
            raise PlaywrightError(
                f"Nao foi possivel confirmar a selecao '{target_text}' no campo 'Objeto do Recurso'."
            )

    def _set_resource_option_value(self, page: Page, value: str) -> None:
        target_text = value.strip()
        if not target_text:
            raise PlaywrightError("Valor de 'Opcao de Recurso' vazio.")

        input_selectors = [
            self.settings.selectors.get("resource_type_field", "").strip(),
            "#ctl00_ContentBody_cpPrincipal_ddlOpcaoRecurso_I",
            "[id*='ddlOpcaoRecurso'][id$='_I']",
            "[id*='OpcaoRecurso'][id$='_I']",
        ]
        combo_keys = [
            "ddlOpcaoRecurso",
            "ctl00_ContentBody_cpPrincipal_ddlOpcaoRecurso",
        ]
        for _ in range(3):
            self._wait_for_cp_principal_idle(page, timeout_ms=5000)
            selected_with_client_api = self._select_resource_option_with_callback(page, combo_keys, target_text)
            if not selected_with_client_api:
                dropdown_selectors = [
                    "#ctl00_ContentBody_cpPrincipal_ddlOpcaoRecurso_B-1",
                    "#ctl00_ContentBody_cpPrincipal_ddlOpcaoRecurso_I",
                    "#ctl00_ContentBody_cpPrincipal_ddlOpcaoRecurso",
                ]
                option_selectors = [
                    f"#ctl00_ContentBody_cpPrincipal_ddlOpcaoRecurso_DDD_L_D td:has-text('{target_text}')",
                    f"#ctl00_ContentBody_cpPrincipal_ddlOpcaoRecurso_DDD_L_D *:has-text('{target_text}')",
                    f"text='{target_text}'",
                ]
                if self._click_first_visible(page, dropdown_selectors, timeout_ms=3500):
                    self._click_first_visible(page, option_selectors, timeout_ms=3500)
                    try:
                        page.evaluate(
                            """() => {
                                if (typeof window.ddlOpcaoRecursoChanged === "function") {
                                    window.ddlOpcaoRecursoChanged();
                                }
                            }"""
                        )
                    except PlaywrightError:
                        pass

            page.wait_for_timeout(400)
            self._confirm_any_popup(page)
            self._wait_for_cp_principal_idle(page, timeout_ms=6000)
            page.wait_for_timeout(300)
            if self._is_resource_option_selected(page, target_text):
                return

        raise PlaywrightError(
            f"Nao foi possivel confirmar a selecao '{target_text}' no campo 'Opcao de Recurso'."
        )

    def _select_resource_option_with_callback(
        self,
        page: Page,
        combo_keys: list[str],
        target_text: str,
    ) -> bool:
        normalized_target = target_text.strip().lower()
        if not normalized_target:
            return False

        unique_keys = [key for key in dict.fromkeys([item.strip() for item in combo_keys if item.strip()])]
        for combo_key in unique_keys:
            try:
                selected = page.evaluate(
                    """({ key, expected }) => {
                        const combo = window[key];
                        if (!combo || typeof combo.GetItemCount !== "function") {
                            return false;
                        }

                        const target = String(expected || "").trim().toLowerCase();
                        const total = combo.GetItemCount();
                        let foundIndex = -1;
                        for (let index = 0; index < total; index += 1) {
                            const item = combo.GetItem(index);
                            if (!item) continue;
                            const text = String(item.text || "").toLowerCase();
                            if (text.includes(target)) {
                                foundIndex = index;
                                break;
                            }
                        }
                        if (foundIndex < 0) {
                            return false;
                        }

                        combo.SetSelectedIndex(foundIndex);
                        if (typeof combo.RaiseValueChanged === "function") {
                            combo.RaiseValueChanged();
                        }
                        if (typeof window.ddlOpcaoRecursoChanged === "function") {
                            try {
                                const originalConfirm = window.confirm;
                                const originalAlert = window.alert;
                                window.confirm = () => true;
                                window.alert = () => {};
                                try {
                                    window.ddlOpcaoRecursoChanged(combo, null);
                                } finally {
                                    window.confirm = originalConfirm;
                                    window.alert = originalAlert;
                                }
                            } catch (error) {
                                return false;
                            }
                        }
                        if (typeof combo.HideDropDown === "function") {
                            combo.HideDropDown();
                        }
                        return true;
                    }""",
                    {"key": combo_key, "expected": normalized_target},
                )
            except PlaywrightError:
                selected = False

            if selected:
                return True

        return False

    def _is_resource_option_selected(self, page: Page, expected_text: str) -> bool:
        input_selectors = [
            self.settings.selectors.get("resource_type_field", "").strip(),
            "#ctl00_ContentBody_cpPrincipal_ddlOpcaoRecurso_I",
            "[id*='ddlOpcaoRecurso'][id$='_I']",
            "[id*='OpcaoRecurso'][id$='_I']",
        ]
        combo_keys = [
            "ddlOpcaoRecurso",
            "ctl00_ContentBody_cpPrincipal_ddlOpcaoRecurso",
        ]
        if not self._is_combo_option_selected(page, input_selectors, combo_keys, expected_text):
            return False

        return True

    def _wait_for_cp_principal_idle(self, page: Page, timeout_ms: int) -> None:
        deadline = monotonic() + (max(timeout_ms, 200) / 1000)
        while monotonic() < deadline:
            try:
                is_idle = page.evaluate(
                    """() => {
                        const panel = window.cpPrincipal;
                        if (!panel || typeof panel.InCallback !== "function") {
                            return true;
                        }
                        return !panel.InCallback();
                    }"""
                )
            except PlaywrightError:
                is_idle = True

            if is_idle:
                return
            page.wait_for_timeout(150)

    def _set_provider_identifier_value(self, page: Page, selectors: list[str], value: str) -> None:
        self._set_value_with_fallbacks(
            page,
            selectors,
            value,
            field_name="Identif. do Prestador",
        )
        if not self._input_contains_expected_value(page, selectors, value):
            try:
                self._set_value_with_fallbacks(
                    page,
                    selectors,
                    value,
                    field_name="Identif. do Prestador",
                )
            except PlaywrightError:
                pass
        if not self._input_contains_expected_value(page, selectors, value):
            raise PlaywrightError(
                "Nao foi possivel confirmar o preenchimento de 'Identif. do Prestador'."
            )
        try:
            page.keyboard.press("Tab")
        except PlaywrightError:
            pass

    def _normalize_provider_identifier(self, value: str) -> str:
        text = value.strip()
        if not text:
            return text

        if re.fullmatch(r"\d+\.0+", text):
            return text.split(".", 1)[0]

        if re.fullmatch(r"[\d\s./-]+", text):
            digits_only = "".join(char for char in text if char.isdigit())
            if digits_only:
                return digits_only

        return text

    def _input_contains_expected_value(self, page: Page, selectors: list[str], expected_value: str) -> bool:
        normalized_expected = expected_value.strip().lower()
        if not normalized_expected:
            return False

        unique_selectors = [selector for selector in dict.fromkeys([item.strip() for item in selectors if item.strip()])]
        frames = [frame for frame in page.frames if not frame.is_detached()]
        for frame in frames:
            for selector in unique_selectors:
                try:
                    locator = frame.locator(selector)
                    count = min(locator.count(), 5)
                except PlaywrightError:
                    continue

                for index in range(count):
                    candidate = locator.nth(index)
                    try:
                        if not candidate.is_visible(timeout=300):
                            continue
                        value = candidate.input_value(timeout=400).strip().lower()
                        if normalized_expected in value:
                            return True
                    except PlaywrightError:
                        continue
        return False

    def _confirm_any_popup(self, page: Page) -> None:
        confirm_selectors = [
            ".swal2-confirm",
            ".bootbox .btn-primary",
            "button:has-text('OK')",
            "button:has-text('Sim')",
            "button:has-text('Confirmar')",
            "a:has-text('OK')",
            "a:has-text('Sim')",
            "a:has-text('Confirmar')",
            "[role='dialog'] button:has-text('OK')",
            "[role='dialog'] button:has-text('Sim')",
            ".ui-dialog button:has-text('OK')",
            ".ui-dialog button:has-text('Sim')",
        ]
        for _ in range(4):
            clicked = self._click_first_visible(page, confirm_selectors, timeout_ms=1200)
            try:
                page.keyboard.press("Enter")
            except PlaywrightError:
                pass
            if not clicked:
                break
            page.wait_for_timeout(250)

    def _add_procedure(self, page: Page, record: ProcedureRecord) -> None:
        self._ensure_resource_option_ready(page)
        self._wait_processing_overlay_to_clear(page, timeout_ms=12000)
        self._wait_for_cp_principal_idle(page, timeout_ms=12000)
        self._open_new_procedure_editor(page)
        page.wait_for_timeout(2000)
        service_date = record.data_realizacao.strftime("%d/%m/%Y")
        self._set_value_with_fallbacks(
            page,
            [
                self.settings.selectors["service_date_field"],
                "#ctl00_ContentBody_cpPrincipal_cpProcedimentos_tbEdicaoItems_DtInicio_I",
                "[id*='tbEdicaoItems_DtInicio_I']",
                "[id*='DtInicio'][id$='_I']",
            ],
            service_date,
            field_name="Data Inicio",
        )
        self._set_value_with_fallbacks(
            page,
            [
                self.settings.selectors.get("service_end_date_field", "").strip(),
                "#ctl00_ContentBody_cpPrincipal_cpProcedimentos_tbEdicaoItems_DtFim_I",
                "[id*='tbEdicaoItems_DtFim_I']",
                "[id*='DtFim'][id$='_I']",
            ],
            service_date,
            field_name="Data Fim",
        )
        self._set_item_gloss_code(page, record.tipo_glosa)
        self._set_item_participation_degree(page, self.settings.grau_participacao_value)
        self._set_value_with_fallbacks(
            page,
            [
                self.settings.selectors["procedure_code_field"],
                "#ctl00_ContentBody_cpPrincipal_cpProcedimentos_tbEdicaoItems_txtCodigoProcedimento_I",
                "[id*='txtCodigoProcedimento'][id$='_I']",
            ],
            record.codigo_procedimento,
            field_name="Codigo",
        )
        self._set_value_with_fallbacks(
            page,
            [
                self.settings.selectors["procedure_description_field"],
                "#ctl00_ContentBody_cpPrincipal_cpProcedimentos_tbEdicaoItems_txtDescricaoProcedimento_I",
                "[id*='txtDescricaoProcedimento'][id$='_I']",
            ],
            record.descricao_procedimento,
            field_name="Descricao",
        )
        self._set_value_with_fallbacks(
            page,
            [
                self.settings.selectors["justification_field"],
                "#ctl00_ContentBody_cpPrincipal_cpProcedimentos_tbEdicaoItems_txtJustificativa_I",
                "[id*='txtJustificativa'][id$='_I']",
            ],
            record.justificativa,
            field_name="Justificativa",
        )
        self._set_value_with_fallbacks(
            page,
            [
                self.settings.selectors["value_field"],
                "#ctl00_ContentBody_cpPrincipal_cpProcedimentos_tbEdicaoItems_txtValorRecursadoItem_I",
                "[id*='txtValorRecursadoItem'][id$='_I']",
            ],
            _format_brl_decimal(record.valor_glosado),
            field_name="Valor Recursado",
        )
        self._include_new_item_procedure(page)

    def _ensure_resource_option_ready(self, page: Page) -> None:
        expected_option = self.settings.resource_option_value
        if self._is_resource_option_selected(page, expected_option):
            return

        for _ in range(2):
            self._set_resource_option_value(page, expected_option)
            self._confirm_any_popup(page)
            self._wait_for_cp_principal_idle(page, timeout_ms=7000)
            page.wait_for_timeout(500)
            if self._is_resource_option_selected(page, expected_option):
                return

        raise PlaywrightError(
            f"Nao foi possivel garantir a selecao '{expected_option}' no campo 'Opcao de Recurso'."
        )

    def _open_new_procedure_editor(self, page: Page) -> None:
        editor_markers = [
            "#ctl00_ContentBody_cpPrincipal_cpProcedimentos_tbEdicaoItems_DtInicio_I",
            "#ctl00_ContentBody_cpPrincipal_cpProcedimentos_tbEdicaoItems_btnIncluirItem_B",
        ]

        plus_selectors = [
            self.settings.selectors["new_procedure_button"],
            "#ctl00_ContentBody_cpPrincipal_cpProcedimentos_tbGridItems_btnNovoItem_B",
            "#ctl00_ContentBody_cpPrincipal_cpProcedimentos_tbGridItems_btnNovoItem_CD",
            "#ctl00_ContentBody_cpPrincipal_cpProcedimentos_tbGridItems_btnNovoItem",
        ]
        for _ in range(4):
            self._wait_processing_overlay_to_clear(page, timeout_ms=12000)
            self._wait_for_cp_principal_idle(page, timeout_ms=12000)
            clicked = self._click_first_visible(page, plus_selectors, timeout_ms=4000)
            if not clicked:
                try:
                    clicked = bool(
                        page.evaluate(
                            """() => {
                                if (typeof window.BtnNovoItem === "function") {
                                    window.BtnNovoItem();
                                    return true;
                                }
                                return false;
                            }"""
                        )
                    )
                except PlaywrightError:
                    clicked = False
            if not clicked:
                page.wait_for_timeout(450)
                continue

            self._wait_for_cp_principal_idle(page, timeout_ms=10000)
            self._wait_processing_overlay_to_clear(page, timeout_ms=10000)
            if any(self._has_visible_selector(page, selector) for selector in editor_markers):
                return
            page.wait_for_timeout(450)

        raise PlaywrightError("Editor de procedimento nao ficou visivel apos acionar o botao de inclusao.")

    def _set_item_gloss_code(self, page: Page, tipo_glosa: str) -> None:
        target_text = tipo_glosa.strip()
        if not target_text:
            raise PlaywrightError("Tipo de glosa vazio para o item.")

        combo_keys = [
            "cboCodGlosaItem",
            "ctl00_ContentBody_cpPrincipal_cpProcedimentos_tbEdicaoItems_cboCodGlosaItem",
        ]
        input_selectors = [
            self.settings.selectors["gloss_code_field"],
            "#ctl00_ContentBody_cpPrincipal_cpProcedimentos_tbEdicaoItems_cboCodGlosaItem_I",
            "[id*='cboCodGlosaItem'][id$='_I']",
        ]
        if not self._select_combo_option_with_client_api(page, combo_keys, target_text):
            self._set_value_with_fallbacks(
                page,
                input_selectors,
                target_text,
                field_name="Cod. Glosa do item",
            )
            try:
                page.keyboard.press("Enter")
            except PlaywrightError:
                pass

        if not self._is_combo_option_selected(page, input_selectors, combo_keys, target_text):
            raise PlaywrightError(f"Nao foi possivel selecionar o codigo de glosa '{target_text}'.")

    def _set_item_participation_degree(self, page: Page, value: str) -> None:
        target_text = value.strip()
        if not target_text:
            raise PlaywrightError("Grau de participacao vazio.")

        combo_keys = [
            "cboGrauParticipacao",
            "ctl00_ContentBody_cpPrincipal_cpProcedimentos_tbEdicaoItems_cboGrauParticipacao",
        ]
        input_selectors = [
            self.settings.selectors["participation_degree_field"],
            "#ctl00_ContentBody_cpPrincipal_cpProcedimentos_tbEdicaoItems_cboGrauParticipacao_I",
            "[id*='cboGrauParticipacao'][id$='_I']",
        ]
        if self._select_combo_option_with_client_api(page, combo_keys, target_text):
            return

        dropdown_selectors = [
            "#ctl00_ContentBody_cpPrincipal_cpProcedimentos_tbEdicaoItems_cboGrauParticipacao_B-1",
            "#ctl00_ContentBody_cpPrincipal_cpProcedimentos_tbEdicaoItems_cboGrauParticipacao_I",
            "#ctl00_ContentBody_cpPrincipal_cpProcedimentos_tbEdicaoItems_cboGrauParticipacao",
        ]
        option_selectors = [
            f"#ctl00_ContentBody_cpPrincipal_cpProcedimentos_tbEdicaoItems_cboGrauParticipacao_DDD_L_D td:has-text('{target_text}')",
            f"#ctl00_ContentBody_cpPrincipal_cpProcedimentos_tbEdicaoItems_cboGrauParticipacao_DDD_L_D *:has-text('{target_text}')",
            f"text='{target_text}'",
        ]
        if self._click_first_visible(page, dropdown_selectors, timeout_ms=3000):
            self._click_first_visible(page, option_selectors, timeout_ms=4000)

        if not self._is_combo_option_selected(page, input_selectors, combo_keys, target_text):
            raise PlaywrightError(
                f"Nao foi possivel selecionar o grau de participacao '{target_text}'."
            )

    def _include_new_item_procedure(self, page: Page) -> None:
        include_selectors = [
            self.settings.selectors.get("save_procedure_button", "").strip(),
            "#ctl00_ContentBody_cpPrincipal_cpProcedimentos_tbEdicaoItems_btnIncluirItem_B",
            "#ctl00_ContentBody_cpPrincipal_cpProcedimentos_tbEdicaoItems_btnIncluirItem_CD",
            "#ctl00_ContentBody_cpPrincipal_cpProcedimentos_tbEdicaoItems_btnIncluirItem",
        ]
        clicked = self._click_first_visible(page, include_selectors, timeout_ms=6000)
        if not clicked:
            try:
                clicked = bool(
                    page.evaluate(
                        """() => {
                            if (typeof window.BtnIncluirItem === "function") {
                                window.BtnIncluirItem();
                                return true;
                            }
                            return false;
                        }"""
                    )
                )
            except PlaywrightError:
                clicked = False

        if not clicked:
            raise PlaywrightError("Nao foi possivel clicar no botao Incluir do procedimento.")

        self._confirm_any_popup(page)
        self._wait_for_cp_principal_idle(page, timeout_ms=10000)
        self._wait_processing_overlay_to_clear(page, timeout_ms=10000)
        page.wait_for_timeout(600)

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

    def _set_value(self, page: Page, selector: str, value: str, timeout_ms: int | None = None) -> None:
        effective_timeout = timeout_ms or self.settings.wait_timeout_ms
        locator = page.locator(selector).first
        locator.wait_for(state="visible", timeout=effective_timeout)
        tag_name = locator.evaluate("el => el.tagName.toLowerCase()")
        text_value = value.strip()

        if tag_name == "select":
            try:
                locator.select_option(label=text_value, timeout=effective_timeout)
            except PlaywrightError:
                locator.select_option(value=text_value, timeout=effective_timeout)
            return

        locator.fill("", timeout=effective_timeout)
        locator.fill(text_value, timeout=effective_timeout)

    def _set_value_with_fallbacks(
        self,
        page: Page,
        selectors: list[str],
        value: str,
        *,
        field_name: str,
    ) -> None:
        last_error: Exception | None = None
        attempted = False
        unique_selectors: list[str] = []

        for selector in selectors:
            normalized = selector.strip()
            if not normalized or normalized in unique_selectors:
                continue
            unique_selectors.append(normalized)

        for selector in unique_selectors:
            attempted = True
            try:
                self._set_value(
                    page,
                    selector,
                    value,
                    timeout_ms=self._fallback_timeout_for_selector_attempts(len(unique_selectors)),
                )
                return
            except PlaywrightError as exc:
                last_error = exc
                continue

        if not attempted:
            raise PlaywrightError(f"Nao ha seletores configurados para o campo '{field_name}'.")

        error_text = str(last_error) if last_error else "Elemento nao ficou visivel em nenhum seletor."
        raise PlaywrightError(f"Nao foi possivel preencher o campo '{field_name}'. {error_text}")

    def _fallback_timeout_for_selector_attempts(self, selector_count: int) -> int:
        safe_count = max(selector_count, 1)
        budget_ms = min(self.settings.wait_timeout_ms, 16000)
        per_selector = max(1200, budget_ms // safe_count)
        return min(per_selector, 4000)

    def _select_combo_option_with_client_api(
        self,
        page: Page,
        combo_keys: list[str],
        target_text: str,
    ) -> bool:
        normalized_target = target_text.strip().lower()
        if not normalized_target:
            return False

        unique_keys = [key for key in dict.fromkeys([item.strip() for item in combo_keys if item.strip()])]
        if not unique_keys:
            return False

        deadline = monotonic() + 4
        while monotonic() < deadline:
            for combo_key in unique_keys:
                try:
                    selected = page.evaluate(
                        """({ key, expected }) => {
                            const combo = window[key];
                            if (!combo || typeof combo.GetItemCount !== "function") {
                                return false;
                            }
                            const target = String(expected || "").trim().toLowerCase();
                            if (!target) {
                                return false;
                            }

                            const total = combo.GetItemCount();
                            for (let index = 0; index < total; index += 1) {
                                const item = combo.GetItem(index);
                                if (!item) {
                                    continue;
                                }
                                const text = String(item.text || "").toLowerCase();
                                if (text.includes(target)) {
                                    combo.SetSelectedIndex(index);
                                    if (typeof combo.RaiseValueChanged === "function") {
                                        combo.RaiseValueChanged();
                                    }
                                    if (typeof combo.HideDropDown === "function") {
                                        combo.HideDropDown();
                                    }
                                    return true;
                                }
                            }
                            return false;
                        }""",
                        {"key": combo_key, "expected": normalized_target},
                    )
                except PlaywrightError:
                    selected = False

                if selected:
                    page.wait_for_timeout(250)
                    return True
            page.wait_for_timeout(120)

        return False

    def _is_combo_option_selected(
        self,
        page: Page,
        input_selectors: list[str],
        combo_keys: list[str],
        expected_text: str,
    ) -> bool:
        normalized_expected = expected_text.strip().lower()
        if not normalized_expected:
            return False

        unique_input_selectors = [
            selector for selector in dict.fromkeys([item.strip() for item in input_selectors if item.strip()])
        ]
        for selector in unique_input_selectors:
            try:
                locator = page.locator(selector)
                count = min(locator.count(), 3)
            except PlaywrightError:
                continue

            for index in range(count):
                candidate = locator.nth(index)
                try:
                    if not candidate.is_visible(timeout=400):
                        continue
                    value = candidate.input_value(timeout=400).strip().lower()
                    if normalized_expected in value:
                        return True
                except PlaywrightError:
                    continue

        unique_combo_keys = [key for key in dict.fromkeys([item.strip() for item in combo_keys if item.strip()])]
        if not unique_combo_keys:
            return False

        for combo_key in unique_combo_keys:
            try:
                matched = page.evaluate(
                    """({ key, expected }) => {
                        const combo = window[key];
                        if (!combo) {
                            return false;
                        }
                        let text = "";
                        if (typeof combo.GetText === "function") {
                            text = combo.GetText() || "";
                        } else if (typeof combo.GetSelectedItem === "function") {
                            const selected = combo.GetSelectedItem();
                            text = selected ? (selected.text || "") : "";
                        }
                        return String(text).toLowerCase().includes(String(expected || "").toLowerCase());
                    }""",
                    {"key": combo_key, "expected": normalized_expected},
                )
            except PlaywrightError:
                matched = False
            if matched:
                return True

        return False

    def _select_operator_and_create_guide(self, page: Page, callbacks: RunCallbacks) -> None:
        selected_operator_code = self.settings.selected_operator_code.strip()
        if not selected_operator_code:
            return

        popup_selector = "#ctl00_ContentBody_ppcCriarNovaGuia_PW-1"
        popup = page.locator(popup_selector).first
        try:
            popup.wait_for(state="visible", timeout=8000)
        except PlaywrightError:
            # Em alguns cenarios a guia pode abrir direto sem popup intermediario.
            return

        callbacks.log(f"Selecionando operadora {selected_operator_code} no popup de nova guia.")

        selected_with_client_api = self._select_operator_with_client_api(page, selected_operator_code)
        if not selected_with_client_api or not self._is_operator_selected(page, selected_operator_code):
            dropdown_selectors = [
                "#ctl00_ContentBody_ppcCriarNovaGuia_ucCriarNovaGuia_cbOperado_B-1",
                "#ctl00_ContentBody_ppcCriarNovaGuia_ucCriarNovaGuia_cbOperado_I",
                "#ctl00_ContentBody_ppcCriarNovaGuia_ucCriarNovaGuia_cbOperado",
            ]
            if not self._click_first_visible(page, dropdown_selectors, timeout_ms=6000):
                raise PlaywrightError("Nao foi possivel abrir a lista de operadoras no popup de nova guia.")

            option_selectors = self._build_operator_exact_item_selectors(selected_operator_code)
            option_selectors.extend(self._build_operator_option_selectors(selected_operator_code))
            if not self._click_first_visible(page, option_selectors, timeout_ms=8000):
                raise PlaywrightError(
                    f"Nao foi possivel selecionar a operadora {selected_operator_code} no popup de nova guia."
                )

        if not self._is_operator_selected(page, selected_operator_code):
            raise PlaywrightError(
                f"A operadora {selected_operator_code} nao foi confirmada no combo de nova guia."
            )
        callbacks.log(f"Operadora {selected_operator_code} confirmada no popup de nova guia.")

        if self._wait_for_guide_form_ready(page, popup, timeout_seconds=2):
            callbacks.log("Formulario de guia ja estava pronto apos selecionar operadora.")
            return

        create_guide_selectors = [
            "#ctl00_ContentBody_ppcCriarNovaGuia_ucCriarNovaGuia_btnCriarGuia_B",
            "#ctl00_ContentBody_ppcCriarNovaGuia_ucCriarNovaGuia_btnCriarGuia_CD",
            "button:has-text('Criar Guia')",
            "text='Criar Guia'",
        ]
        clicked_create_guide = self._click_first_visible(page, create_guide_selectors, timeout_ms=6000)
        if not clicked_create_guide:
            clicked_create_guide = self._click_create_guide_with_script(page)

        if clicked_create_guide:
            try:
                popup.wait_for(state="hidden", timeout=self.settings.wait_timeout_ms)
            except PlaywrightError:
                callbacks.log(
                    "Popup de nova guia permaneceu aberto apos clicar em 'Criar Guia'. "
                    "Tentando continuar o preenchimento."
                )

        if self._wait_for_guide_form_ready(page, popup):
            if clicked_create_guide:
                callbacks.log("Formulario de guia pronto apos tentativa de criacao.")
            else:
                callbacks.log(
                    "Popup de nova guia nao exigiu clique explicito no botao 'Criar Guia'. "
                    "Formulario da guia ja esta pronto."
                )
            return

        if clicked_create_guide:
            callbacks.log(
                "Clique no botao 'Criar Guia' foi executado, mas a tela da guia nao ficou pronta."
            )
            raise PlaywrightError("Clique em 'Criar Guia' executado, mas o formulario nao foi carregado.")

        raise PlaywrightError("Nao foi possivel clicar no botao 'Criar Guia'.")

    def _select_operator_with_client_api(self, page: Page, selected_operator_code: str) -> bool:
        combo_key = "ctl00_ContentBody_ppcCriarNovaGuia_ucCriarNovaGuia_cbOperado"
        deadline = monotonic() + 6

        while monotonic() < deadline:
            try:
                selected = page.evaluate(
                    """({ key, selectedCode }) => {
                        const combo = window[key];
                        if (!combo || typeof combo.GetItemCount !== "function") {
                            return false;
                        }

                        const normalizeDigits = (value) => String(value || "").replace(/\\D/g, "");
                        const inputDigits = normalizeDigits(selectedCode);
                        if (!inputDigits) {
                            return false;
                        }

                        const canonical = String(Number(inputDigits));
                        const codes = new Set([inputDigits, canonical, canonical.padStart(6, "0")]);
                        if (canonical === "5711") {
                            codes.add("005711");
                        }

                        const matchesCode = (text) => {
                            const normalizedText = String(text || "");
                            const textDigits = normalizeDigits(normalizedText);
                            for (const code of codes) {
                                if (!code) continue;
                                if (normalizedText.includes(code) || textDigits.includes(code)) {
                                    return true;
                                }
                            }
                            return false;
                        };

                        const total = combo.GetItemCount();
                        for (let index = 0; index < total; index += 1) {
                            const item = combo.GetItem(index);
                            if (!item) {
                                continue;
                            }
                            if (matchesCode(item.text)) {
                                combo.SetSelectedIndex(index);
                                if (typeof combo.RaiseValueChanged === "function") {
                                    combo.RaiseValueChanged();
                                }
                                return true;
                            }
                        }

                        return false;
                    }""",
                    {"key": combo_key, "selectedCode": selected_operator_code},
                )
            except PlaywrightError:
                selected = False

            if selected:
                page.wait_for_timeout(350)
                if self._is_operator_selected(page, selected_operator_code):
                    return True
            page.wait_for_timeout(180)

        return False

    def _is_operator_selected(self, page: Page, selected_operator_code: str) -> bool:
        expected_codes = self._build_operator_code_candidates(selected_operator_code)
        if not expected_codes:
            return False

        try:
            current_text = page.locator(
                "#ctl00_ContentBody_ppcCriarNovaGuia_ucCriarNovaGuia_cbOperado_I"
            ).first.input_value(timeout=1200)
        except PlaywrightError:
            current_text = ""

        normalized_text = "".join(ch for ch in current_text if ch.isdigit())
        if normalized_text and any(code in normalized_text for code in expected_codes):
            return True

        combo_key = "ctl00_ContentBody_ppcCriarNovaGuia_ucCriarNovaGuia_cbOperado"
        try:
            combo_text = page.evaluate(
                """(key) => {
                    const combo = window[key];
                    if (!combo) {
                        return "";
                    }
                    if (typeof combo.GetText === "function") {
                        return combo.GetText() || "";
                    }
                    if (typeof combo.GetSelectedItem === "function") {
                        const item = combo.GetSelectedItem();
                        return item ? (item.text || "") : "";
                    }
                    return "";
                }""",
                combo_key,
            )
        except PlaywrightError:
            combo_text = ""

        normalized_combo_text = "".join(ch for ch in str(combo_text) if ch.isdigit())
        return bool(normalized_combo_text and any(code in normalized_combo_text for code in expected_codes))

    def _build_operator_exact_item_selectors(self, selected_operator_code: str) -> list[str]:
        canonical_code = (("".join(ch for ch in selected_operator_code if ch.isdigit())).lstrip("0") or "0")
        base = "#ctl00_ContentBody_ppcCriarNovaGuia_ucCriarNovaGuia_cbOperado_DDD_L_"
        mapping = {
            "421715": ["LBI1T", "LBI1"],
            "5711": ["LBI2T", "LBI2"],
            "333689": ["LBI3T", "LBI3"],
        }
        suffixes = mapping.get(canonical_code, [])
        return [f"{base}{suffix}" for suffix in suffixes]

    def _build_operator_code_candidates(self, selected_operator_code: str) -> list[str]:
        normalized_digits = "".join(ch for ch in selected_operator_code if ch.isdigit())
        if not normalized_digits:
            return []

        canonical_code = normalized_digits.lstrip("0") or "0"
        candidates = [canonical_code]
        padded = canonical_code.zfill(6)
        if padded not in candidates:
            candidates.insert(0, padded)
        if normalized_digits not in candidates:
            candidates.insert(0, normalized_digits)

        if canonical_code == "5711" and "005711" not in candidates:
            candidates.insert(0, "005711")

        unique_candidates: list[str] = []
        for candidate in candidates:
            if candidate and candidate not in unique_candidates:
                unique_candidates.append(candidate)

        return unique_candidates

    def _build_operator_option_selectors(self, selected_operator_code: str) -> list[str]:
        unique_candidates = self._build_operator_code_candidates(selected_operator_code)
        if not unique_candidates:
            return []

        base_list_selector = "#ctl00_ContentBody_ppcCriarNovaGuia_ucCriarNovaGuia_cbOperado_DDD_L_D"
        selectors: list[str] = []
        for candidate in unique_candidates:
            selectors.append(f"{base_list_selector} td[id*='LBI']:has-text('{candidate}')")
            selectors.append(f"{base_list_selector} td:has-text('{candidate}')")
            selectors.append(f"{base_list_selector} *:has-text('{candidate}')")
            selectors.append(f"text=/ans\\s*:\\s*{candidate}/i")

        return selectors

    def _click_first_visible(self, page: Page, selectors: list[str], timeout_ms: int) -> bool:
        deadline = monotonic() + (timeout_ms / 1000)

        while monotonic() < deadline:
            frames = [frame for frame in page.frames if not frame.is_detached()]
            for frame in frames:
                for selector in selectors:
                    try:
                        locator = frame.locator(selector)
                        count = min(locator.count(), 8)
                    except PlaywrightError:
                        continue

                    for index in range(count):
                        candidate = locator.nth(index)
                        try:
                            if not candidate.is_visible(timeout=300):
                                continue
                            try:
                                candidate.click(timeout=1500)
                            except PlaywrightError:
                                candidate.click(timeout=1500, force=True)
                            return True
                        except PlaywrightError:
                            continue

            page.wait_for_timeout(150)

        return False

    def _click_create_guide_with_script(self, page: Page) -> bool:
        button_ids = [
            "ctl00_ContentBody_ppcCriarNovaGuia_ucCriarNovaGuia_btnCriarGuia_B",
            "ctl00_ContentBody_ppcCriarNovaGuia_ucCriarNovaGuia_btnCriarGuia_CD",
        ]
        for button_id in button_ids:
            try:
                clicked = page.evaluate(
                    """(targetId) => {
                        const target = document.getElementById(targetId);
                        if (!target) {
                            return false;
                        }
                        target.click();
                        return true;
                    }""",
                    button_id,
                )
            except PlaywrightError:
                clicked = False
            if clicked:
                page.wait_for_timeout(250)
                return True
        return False

    def _wait_for_guide_form_ready(self, page: Page, popup_locator, timeout_seconds: float = 12) -> bool:
        deadline = monotonic() + max(timeout_seconds, 0.5)
        while monotonic() < deadline:
            popup_still_visible = False
            try:
                popup_still_visible = popup_locator.is_visible(timeout=300)
            except PlaywrightError:
                popup_still_visible = False

            if not popup_still_visible and self._is_guide_form_visible(page):
                return True

            page.wait_for_timeout(250)

        return False

    def _is_guide_form_visible(self, page: Page) -> bool:
        configured_markers = (
            self.settings.selectors.get("object_resource_field", "").strip(),
            self.settings.selectors.get("protocol_number_field", "").strip(),
            self.settings.selectors.get("provider_identifier_field", "").strip(),
        )
        for selector in configured_markers:
            if selector and self._has_visible_selector(page, selector):
                return True

        structural_markers = (
            "text=/recurso\\s+de\\s+glosa/i",
            "text=/protocolo\\s+de\\s+faturamento/i",
            "text=/identif\\.?\\s*do\\s*prestador/i",
            "text=/dados\\s+do\\s+contratado/i",
        )
        visible_markers = 0
        for selector in structural_markers:
            if self._has_visible_selector(page, selector):
                visible_markers += 1
                if visible_markers >= 2:
                    return True

        return False

    def _has_visible_selector(self, page: Page, selector: str) -> bool:
        if not selector:
            return False

        frames = [frame for frame in page.frames if not frame.is_detached()]
        for frame in frames:
            try:
                locator = frame.locator(selector)
                count = min(locator.count(), 10)
            except PlaywrightError:
                continue

            for index in range(count):
                candidate = locator.nth(index)
                try:
                    if candidate.is_visible(timeout=600):
                        return True
                except PlaywrightError:
                    continue

        return False

    def _wait_processing_overlay_to_clear(self, page: Page, timeout_ms: int = 10000) -> None:
        deadline = monotonic() + (max(timeout_ms, 200) / 1000)
        while monotonic() < deadline:
            if not self._has_visible_selector(page, "text=/processando/i"):
                return
            page.wait_for_timeout(150)

    def _normalize_password_key(self, senha: str) -> str:
        return senha.strip().upper()

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
        if self.settings.selected_operator_code:
            allowed_operator_codes = {"5711", "421715", "333689"}
            if self.settings.selected_operator_code not in allowed_operator_codes:
                raise AutomationConfigurationError(
                    "Valor invalido para 'bot.selected_operator_code'. "
                    "Use 5711, 421715 ou 333689."
                )

        if not self.settings.object_resource_value.strip():
            raise AutomationConfigurationError(
                "Configure 'bot.object_resource_value' no arquivo config/config.json."
            )
        if not self.settings.resource_option_value.strip():
            raise AutomationConfigurationError(
                "Configure 'bot.resource_option_value' no arquivo config/config.json."
            )
        if not self.settings.grau_participacao_value.strip():
            raise AutomationConfigurationError(
                "Configure 'bot.grau_participacao_value' no arquivo config/config.json."
            )
        if self.settings.error_mode not in {"tolerant", "strict"}:
            raise AutomationConfigurationError("Valor invalido para 'bot.error_mode'. Use 'tolerant' ou 'strict'.")
        if self.settings.login_mode not in {"manual", "automatic"}:
            raise AutomationConfigurationError("Valor invalido para 'bot.login_mode'. Use 'manual' ou 'automatic'.")
        if self.settings.execution_mode not in {"full", "header_protocol_only"}:
            raise AutomationConfigurationError(
                "Valor invalido para 'bot.execution_mode'. Use 'full' ou 'header_protocol_only'."
            )
        if self.settings.use_existing_browser and not self.settings.existing_browser_cdp_url.strip():
            raise AutomationConfigurationError(
                "Configure 'bot.existing_browser_cdp_url' para conectar no navegador existente."
            )
        if self.settings.start_from_current_page and not self.settings.use_existing_browser:
            raise AutomationConfigurationError(
                "Para usar 'bot.start_from_current_page', habilite tambem 'bot.use_existing_browser'."
            )

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
