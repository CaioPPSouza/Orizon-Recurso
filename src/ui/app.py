from __future__ import annotations

import logging
import queue
from datetime import datetime
from pathlib import Path
from threading import Event, Thread
from tkinter import END, DISABLED, NORMAL, StringVar, Tk, filedialog, ttk
from tkinter.scrolledtext import ScrolledText
from typing import Any

from src.bot import AutomationConfigurationError, OrizonAutomator, RunCallbacks, StopRequested
from src.configuration import build_bot_settings, get_sheet_name, load_runtime_config
from src.core import SpreadsheetValidationError, load_and_group_spreadsheet


class MainWindow(Tk):
    def __init__(self):
        super().__init__()
        self.title("Automacao de Recursos de Glosa - ORIZON")
        self.geometry("1100x760")
        self.minsize(980, 640)
        ttk.Style(self).theme_use("clam")

        self.file_path_var = StringVar(value="")
        self.operator_code_var = StringVar(value="")
        self.status_var = StringVar(value="Aguardando")
        self.current_line_var = StringVar(value="-")
        self.current_password_var = StringVar(value="-")
        self.log_file_var = StringVar(value="-")

        self.counter_vars: dict[str, StringVar] = {
            "total_rows": StringVar(value="0"),
            "total_guides": StringVar(value="0"),
            "guides_completed": StringVar(value="0"),
            "procedures_included": StringVar(value="0"),
            "successes": StringVar(value="0"),
            "errors": StringVar(value="0"),
        }

        self._event_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._worker_thread: Thread | None = None
        self._stop_event = Event()

        self._build_layout()
        self.after(120, self._process_events)

    def _build_layout(self) -> None:
        main = ttk.Frame(self, padding=16)
        main.pack(fill="both", expand=True)

        file_frame = ttk.LabelFrame(main, text="Selecao de planilha", padding=12)
        file_frame.pack(fill="x")

        ttk.Button(file_frame, text="Selecionar planilha (.xlsx)", command=self._select_file).pack(side="left")
        ttk.Label(file_frame, textvariable=self.file_path_var, width=95).pack(side="left", padx=12)

        control_frame = ttk.LabelFrame(main, text="Controle de execucao", padding=12)
        control_frame.pack(fill="x", pady=10)

        self.start_button = ttk.Button(control_frame, text="Iniciar", command=self._start_execution, state=DISABLED)
        self.start_button.pack(side="left")

        self.stop_button = ttk.Button(control_frame, text="Parar", command=self._request_stop, state=DISABLED)
        self.stop_button.pack(side="left", padx=8)

        ttk.Label(control_frame, text="Operadora:").pack(side="left", padx=(18, 6))
        self.operator_combo = ttk.Combobox(
            control_frame,
            textvariable=self.operator_code_var,
            values=("5711", "421715", "333689"),
            state="readonly",
            width=10,
        )
        self.operator_combo.pack(side="left")
        self.operator_combo.bind("<<ComboboxSelected>>", self._on_operator_selected)

        status_frame = ttk.LabelFrame(main, text="Status e indicadores", padding=12)
        status_frame.pack(fill="x")

        ttk.Label(status_frame, text="Status:").grid(row=0, column=0, sticky="w")
        ttk.Label(status_frame, textvariable=self.status_var).grid(row=0, column=1, sticky="w", padx=8)
        ttk.Label(status_frame, text="Linha atual:").grid(row=0, column=2, sticky="w", padx=(24, 0))
        ttk.Label(status_frame, textvariable=self.current_line_var).grid(row=0, column=3, sticky="w", padx=8)
        ttk.Label(status_frame, text="Senha atual:").grid(row=0, column=4, sticky="w", padx=(24, 0))
        ttk.Label(status_frame, textvariable=self.current_password_var).grid(row=0, column=5, sticky="w", padx=8)

        labels = [
            ("Total de linhas lidas", "total_rows"),
            ("Total de guias previstas", "total_guides"),
            ("Guias concluidas", "guides_completed"),
            ("Procedimentos incluidos", "procedures_included"),
            ("Sucessos", "successes"),
            ("Erros", "errors"),
        ]

        for index, (label, key) in enumerate(labels):
            row = 1 + (index // 3)
            col = (index % 3) * 2
            ttk.Label(status_frame, text=f"{label}:").grid(row=row, column=col, sticky="w", pady=(8, 0))
            ttk.Label(status_frame, textvariable=self.counter_vars[key]).grid(
                row=row,
                column=col + 1,
                sticky="w",
                padx=8,
                pady=(8, 0),
            )

        ttk.Label(status_frame, text="Arquivo de log:").grid(row=3, column=0, sticky="w", pady=(8, 0))
        ttk.Label(status_frame, textvariable=self.log_file_var).grid(row=3, column=1, columnspan=5, sticky="w", pady=(8, 0))

        log_frame = ttk.LabelFrame(main, text="Log em tempo real", padding=12)
        log_frame.pack(fill="both", expand=True, pady=10)

        self.log_output = ScrolledText(log_frame, wrap="word", height=18, state=DISABLED)
        self.log_output.pack(fill="both", expand=True)

    def _select_file(self) -> None:
        selected = filedialog.askopenfilename(
            title="Selecione a planilha",
            filetypes=[("Planilha Excel", "*.xlsx")],
        )
        if not selected:
            return

        self.file_path_var.set(selected)
        self._set_status("Validando planilha")
        self._append_log(f"Arquivo selecionado: {selected}")

        try:
            config = load_runtime_config()
            sheet_name = get_sheet_name(config)
            spreadsheet_data = load_and_group_spreadsheet(selected, sheet_name=sheet_name)
            self._set_counter("total_rows", spreadsheet_data.summary.total_rows)
            self._set_counter("total_guides", spreadsheet_data.summary.total_guides)
            self._set_counter("guides_completed", 0)
            self._set_counter("procedures_included", 0)
            self._set_counter("successes", 0)
            self._set_counter("errors", 0)
            self.current_line_var.set("-")
            self.current_password_var.set("-")
            self._append_log(
                f"Planilha validada: {spreadsheet_data.summary.total_rows} linha(s), "
                f"{spreadsheet_data.summary.total_guides} guia(s) prevista(s)."
            )
            self._set_status("Aguardando")
            self._refresh_start_button_state()
        except SpreadsheetValidationError as exc:
            self._set_status("Erro")
            self._append_log(f"Erro ao validar planilha: {exc}")
            self.start_button.config(state=DISABLED)
        except Exception as exc:
            self._set_status("Erro")
            self._append_log(f"Falha inesperada na validacao: {exc}")
            self.start_button.config(state=DISABLED)

    def _start_execution(self) -> None:
        if self._worker_thread and self._worker_thread.is_alive():
            return

        selected = self.file_path_var.get().strip()
        if not selected:
            self._append_log("Selecione uma planilha antes de iniciar.")
            return
        selected_operator_code = self.operator_code_var.get().strip()
        if not selected_operator_code:
            self._append_log("Selecione a operadora (5711, 421715 ou 333689) antes de iniciar.")
            return

        self._stop_event.clear()
        self._set_counter("guides_completed", 0)
        self._set_counter("procedures_included", 0)
        self._set_counter("successes", 0)
        self._set_counter("errors", 0)
        self.current_line_var.set("-")
        self.current_password_var.set("-")

        self.start_button.config(state=DISABLED)
        self.stop_button.config(state=NORMAL)
        self._set_status("Executando")
        self._append_log(f"Execucao iniciada para operadora {selected_operator_code}.")

        self._worker_thread = Thread(target=self._run_worker, args=(selected, selected_operator_code), daemon=True)
        self._worker_thread.start()

    def _request_stop(self) -> None:
        if not (self._worker_thread and self._worker_thread.is_alive()):
            return
        self._stop_event.set()
        self._set_status("Parando")
        self._append_log("Parada solicitada. O sistema vai finalizar a acao atual e encerrar.")

    def _run_worker(self, file_path: str, selected_operator_code: str) -> None:
        logger = _create_run_logger()
        log_file_path = str(_extract_log_path(logger))
        self._event_queue.put(("log_file", log_file_path))
        self._event_queue.put(("log", f"Log em arquivo: {log_file_path}"))

        def log(message: str) -> None:
            logger.info(message)
            self._event_queue.put(("log", message))

        try:
            config = load_runtime_config()
            config.setdefault("bot", {})["selected_operator_code"] = selected_operator_code
            sheet_name = get_sheet_name(config)
            spreadsheet_data = load_and_group_spreadsheet(file_path, sheet_name=sheet_name)

            self._event_queue.put(("counter_set", ("total_rows", spreadsheet_data.summary.total_rows)))
            self._event_queue.put(("counter_set", ("total_guides", spreadsheet_data.summary.total_guides)))
            self._event_queue.put(("status", "Executando"))
            self._event_queue.put(
                (
                    "log",
                    f"Planilha pronta para execucao: {spreadsheet_data.summary.total_rows} linha(s), "
                    f"{spreadsheet_data.summary.total_guides} senha(s). Operadora: {selected_operator_code}.",
                )
            )

            bot_settings = build_bot_settings(config)
            automator = OrizonAutomator(bot_settings)

            callbacks = RunCallbacks(
                log=log,
                on_guide_start=lambda group: self._event_queue.put(("current_password", group.senha)),
                on_guide_success=lambda _group: (
                    self._event_queue.put(("counter_increment", ("guides_completed", 1))),
                    self._event_queue.put(("counter_increment", ("successes", 1))),
                ),
                on_guide_error=lambda group, exc: self._handle_guide_error(group.senha, str(exc), logger),
                on_procedure_success=lambda _group, record: self._on_procedure_success(record.row_number),
                on_procedure_error=lambda group, record, exc: self._handle_procedure_error(
                    group.senha, record.row_number, str(exc), logger
                ),
            )

            automator.run(spreadsheet_data.groups, self._stop_event, callbacks)

            if self._stop_event.is_set():
                self._event_queue.put(("status", "Parado pelo usuario"))
                self._event_queue.put(("log", "Execucao interrompida pelo usuario."))
            else:
                self._event_queue.put(("status", "Concluido"))
                self._event_queue.put(("log", "Execucao concluida com sucesso."))
        except StopRequested:
            logger.info("Execucao interrompida por solicitacao do usuario.")
            self._event_queue.put(("status", "Parado pelo usuario"))
            self._event_queue.put(("log", "Execucao interrompida pelo usuario."))
        except (SpreadsheetValidationError, AutomationConfigurationError) as exc:
            logger.error("Erro de validacao: %s", exc)
            self._event_queue.put(("status", "Erro"))
            self._event_queue.put(("counter_increment", ("errors", 1)))
            self._event_queue.put(("log", f"Erro de configuracao/validacao: {exc}"))
        except Exception as exc:
            logger.exception("Falha inesperada durante a execucao.")
            self._event_queue.put(("status", "Erro"))
            self._event_queue.put(("counter_increment", ("errors", 1)))
            self._event_queue.put(("log", f"Falha inesperada: {exc}"))
        finally:
            self._event_queue.put(("execution_finished", None))

    def _handle_guide_error(self, senha: str, reason: str, logger: logging.Logger) -> None:
        logger.error("Guia com senha %s falhou: %s", senha, reason)
        self._event_queue.put(("counter_increment", ("errors", 1)))
        self._event_queue.put(("log", f"Erro na guia da senha {senha}: {reason}"))

    def _handle_procedure_error(self, senha: str, row_number: int, reason: str, logger: logging.Logger) -> None:
        logger.error("Procedimento da senha %s na linha %s falhou: %s", senha, row_number, reason)
        self._event_queue.put(("counter_increment", ("errors", 1)))
        self._event_queue.put(("log", f"Erro no procedimento da senha {senha}, linha {row_number}: {reason}"))

    def _on_procedure_success(self, row_number: int) -> None:
        self._event_queue.put(("current_line", str(row_number)))
        self._event_queue.put(("counter_increment", ("procedures_included", 1)))
        self._event_queue.put(("counter_increment", ("successes", 1)))

    def _process_events(self) -> None:
        while True:
            try:
                event, payload = self._event_queue.get_nowait()
            except queue.Empty:
                break

            if event == "log":
                self._append_log(str(payload))
            elif event == "status":
                self._set_status(str(payload))
            elif event == "counter_set":
                key, value = payload
                self._set_counter(key, int(value))
            elif event == "counter_increment":
                key, delta = payload
                self._increment_counter(key, int(delta))
            elif event == "current_line":
                self.current_line_var.set(str(payload))
            elif event == "current_password":
                self.current_password_var.set(str(payload))
            elif event == "log_file":
                self.log_file_var.set(str(payload))
            elif event == "execution_finished":
                self._refresh_start_button_state()
                self.stop_button.config(state=DISABLED)

        self.after(120, self._process_events)

    def _set_status(self, value: str) -> None:
        self.status_var.set(value)

    def _set_counter(self, key: str, value: int) -> None:
        self.counter_vars[key].set(str(value))

    def _increment_counter(self, key: str, delta: int) -> None:
        current = int(self.counter_vars[key].get())
        self.counter_vars[key].set(str(current + delta))

    def _append_log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        line = f"[{timestamp}] {message}\n"
        self.log_output.config(state=NORMAL)
        self.log_output.insert(END, line)
        self.log_output.see(END)
        self.log_output.config(state=DISABLED)

    def _on_operator_selected(self, _event=None) -> None:
        selected_operator_code = self.operator_code_var.get().strip()
        if selected_operator_code:
            self._append_log(f"Operadora selecionada: {selected_operator_code}.")
        self._refresh_start_button_state()

    def _refresh_start_button_state(self) -> None:
        can_start = (
            bool(self.file_path_var.get().strip())
            and bool(self.operator_code_var.get().strip())
            and not (self._worker_thread and self._worker_thread.is_alive())
        )
        self.start_button.config(state=NORMAL if can_start else DISABLED)


def _create_run_logger() -> logging.Logger:
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    logger_name = f"orizon_run_{run_id}"
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.INFO)
    logger.propagate = False

    log_dir = Path("logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"execution_{run_id}.log"

    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
    logger.addHandler(handler)
    return logger


def _extract_log_path(logger: logging.Logger) -> Path:
    for handler in logger.handlers:
        if isinstance(handler, logging.FileHandler):
            return Path(handler.baseFilename)
    return Path("-")


def run_app() -> None:
    window = MainWindow()
    window.mainloop()
