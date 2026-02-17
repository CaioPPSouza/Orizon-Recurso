from __future__ import annotations

import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

try:
    import pandas as pd
except ModuleNotFoundError:
    pd = None

from .models import GuideGroup, ProcedureRecord, SpreadsheetSummary


class SpreadsheetValidationError(Exception):
    """Erro de validação de planilha."""


@dataclass(frozen=True)
class SpreadsheetData:
    groups: list[GuideGroup]
    summary: SpreadsheetSummary


COLUMN_ALIASES: dict[str, list[str]] = {
    "numero_lote": ["Numero Lote", "Número Lote"],
    "prestador_numero": ["PrestadorNumero", "Prestador Número", "PrestadorNúmero"],
    "protocolo_numero": ["ProtocoloNumero", "Protocolo Número", "ProtocoloNúmero"],
    "guia_prestador": ["Guia Prestador", "GuiaPrestador"],
    "senha": ["Senha"],
    "numero_guia_operadora": [
        "Numero Guia Operadora",
        "Número Guia Operadora",
        "NumeroGuiaOperadora",
        "NúmeroGuiaOperadora",
    ],
    "data_realizacao": ["Data Realizacao", "Data Realização", "DataRealizacao", "DataRealização"],
    "tipo_glosa": ["Tipo Glosa", "TipoGlosa"],
    "codigo_procedimento": ["Codigo Procedimento", "Código Procedimento", "CodigoProcedimento", "CódigoProcedimento"],
    "descricao_procedimento": [
        "Descricao Procedimento",
        "Descrição Procedimento",
        "DescricaoProcedimento",
        "DescriçãoProcedimento",
    ],
    "valor_glosado": ["Valor Glosado", "ValorGlosado"],
    "justificativa": ["Justificativa"],
}

REQUIRED_COLUMNS = {
    "prestador_numero",
    "protocolo_numero",
    "guia_prestador",
    "senha",
    "numero_guia_operadora",
    "data_realizacao",
    "tipo_glosa",
    "codigo_procedimento",
    "descricao_procedimento",
    "valor_glosado",
    "justificativa",
}


def load_and_group_spreadsheet(path: str | Path, sheet_name: str = "Planilha Analisada") -> SpreadsheetData:
    if pd is None:
        raise SpreadsheetValidationError(
            "Dependencia ausente: instale as dependencias com 'pip install -r requirements.txt'."
        )

    spreadsheet_path = Path(path)
    if not spreadsheet_path.exists():
        raise SpreadsheetValidationError(f"Arquivo nao encontrado: {spreadsheet_path}")

    try:
        dataframe = pd.read_excel(spreadsheet_path, sheet_name=sheet_name)
    except ValueError as exc:
        raise SpreadsheetValidationError(f"A aba '{sheet_name}' nao foi encontrada na planilha.") from exc
    except Exception as exc:
        raise SpreadsheetValidationError(f"Falha ao ler planilha: {exc}") from exc

    if dataframe.empty:
        raise SpreadsheetValidationError("A planilha nao possui linhas para processamento.")

    normalized = _normalize_columns(dataframe)
    _validate_required_columns(normalized.columns)
    normalized = _drop_fully_empty_rows(normalized)
    records = _parse_rows(normalized)
    groups = _group_records(records)

    summary = SpreadsheetSummary(
        total_rows=len(records),
        total_guides=len(groups),
        total_procedures=len(records),
    )
    return SpreadsheetData(groups=groups, summary=summary)


def _normalize_columns(dataframe: pd.DataFrame) -> pd.DataFrame:
    normalized_map: dict[str, str] = {}
    alias_to_key = _build_alias_map()

    for column in dataframe.columns:
        normalized_name = _canonical_text(str(column))
        normalized_map[column] = alias_to_key.get(normalized_name, normalized_name)

    return dataframe.rename(columns=normalized_map)


def _build_alias_map() -> dict[str, str]:
    alias_map: dict[str, str] = {}
    for canonical_key, aliases in COLUMN_ALIASES.items():
        alias_map[_canonical_text(canonical_key)] = canonical_key
        for alias in aliases:
            alias_map[_canonical_text(alias)] = canonical_key
    return alias_map


def _canonical_text(value: str) -> str:
    stripped = value.strip().lower()
    without_accents = "".join(
        char for char in unicodedata.normalize("NFD", stripped) if unicodedata.category(char) != "Mn"
    )
    return "".join(char for char in without_accents if char.isalnum())


def _validate_required_columns(columns: pd.Index) -> None:
    missing = sorted(REQUIRED_COLUMNS - set(columns))
    if missing:
        formatted = ", ".join(missing)
        raise SpreadsheetValidationError(f"Colunas obrigatorias ausentes: {formatted}")


def _drop_fully_empty_rows(dataframe: pd.DataFrame) -> pd.DataFrame:
    relevant_columns = [column for column in REQUIRED_COLUMNS if column in dataframe.columns]
    if not relevant_columns:
        return dataframe
    filtered = dataframe.dropna(how="all", subset=relevant_columns)
    if filtered.empty:
        raise SpreadsheetValidationError("A planilha nao possui linhas validas apos remover linhas vazias.")
    return filtered


def _parse_rows(dataframe: pd.DataFrame) -> list[ProcedureRecord]:
    parsed_rows: list[ProcedureRecord] = []
    errors: list[str] = []

    working = dataframe.copy()
    working["__row_number__"] = working.index + 2
    working["__date_order__"] = working["data_realizacao"].apply(_parse_date_for_order)
    working = working.sort_values(by=["senha", "__date_order__", "__row_number__"], kind="stable")

    for _, row in working.iterrows():
        row_number = int(row["__row_number__"])
        try:
            record = ProcedureRecord(
                row_number=row_number,
                numero_lote=_optional_text(row.get("numero_lote")),
                prestador_numero=_required_text(row.get("prestador_numero"), "prestador_numero"),
                protocolo_numero=_required_text(row.get("protocolo_numero"), "protocolo_numero"),
                guia_prestador=_required_text(row.get("guia_prestador"), "guia_prestador"),
                senha=_required_text(row.get("senha"), "senha"),
                numero_guia_operadora=_required_text(row.get("numero_guia_operadora"), "numero_guia_operadora"),
                data_realizacao=_required_date(row.get("data_realizacao"), "data_realizacao"),
                tipo_glosa=_required_text(row.get("tipo_glosa"), "tipo_glosa"),
                codigo_procedimento=_required_text(row.get("codigo_procedimento"), "codigo_procedimento"),
                descricao_procedimento=_required_text(row.get("descricao_procedimento"), "descricao_procedimento"),
                valor_glosado=_required_decimal(row.get("valor_glosado"), "valor_glosado"),
                justificativa=_required_text(row.get("justificativa"), "justificativa"),
            )
            parsed_rows.append(record)
        except SpreadsheetValidationError as exc:
            errors.append(f"Linha {row_number}: {exc}")

    if errors:
        preview = "\n".join(errors[:20])
        suffix = "\n..." if len(errors) > 20 else ""
        raise SpreadsheetValidationError(
            f"Foram encontrados erros de validacao em {len(errors)} linha(s):\n{preview}{suffix}"
        )

    return parsed_rows


def _group_records(records: list[ProcedureRecord]) -> list[GuideGroup]:
    grouped: dict[str, list[ProcedureRecord]] = defaultdict(list)
    for record in records:
        grouped[record.senha].append(record)
    return [GuideGroup(senha=senha, records=rows) for senha, rows in grouped.items()]


def _required_text(value: Any, field_name: str) -> str:
    text = _optional_text(value)
    if not text:
        raise SpreadsheetValidationError(f"Campo '{field_name}' vazio.")
    return text


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def _required_date(value: Any, field_name: str) -> datetime:
    parsed = _parse_date(value)
    if parsed is None:
        raise SpreadsheetValidationError(f"Campo '{field_name}' possui data invalida.")
    return parsed


def _parse_date_for_order(value: Any) -> datetime:
    parsed = _parse_date(value)
    return parsed if parsed is not None else datetime.max


def _parse_date(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, pd.Timestamp):
        return value.to_pydatetime()

    text = _optional_text(value)
    if not text:
        return None

    formats = ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y %H:%M:%S")
    for date_format in formats:
        try:
            return datetime.strptime(text, date_format)
        except ValueError:
            continue

    try:
        return pd.to_datetime(text, dayfirst=True).to_pydatetime()
    except Exception:
        return None


def _required_decimal(value: Any, field_name: str) -> Decimal:
    parsed = _parse_decimal(value)
    if parsed is None:
        raise SpreadsheetValidationError(f"Campo '{field_name}' possui valor invalido.")
    return parsed


def _parse_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        parsed_float = Decimal(str(value))
        # Planilhas em pt-BR podem ser lidas como float quando o valor original era "1.234" (milhar).
        if parsed_float.as_tuple().exponent == -3 and abs(parsed_float) >= 1:
            return parsed_float * Decimal("1000")
        return parsed_float

    text = _optional_text(value)
    if not text:
        return None

    normalized = text.replace("R$", "").replace(" ", "").replace("\u00A0", "")
    if not normalized:
        return None

    negative = False
    if normalized.startswith("(") and normalized.endswith(")"):
        negative = True
        normalized = normalized[1:-1]

    if normalized.startswith("-"):
        negative = True
        normalized = normalized[1:]

    if "," in normalized and "." in normalized:
        normalized = normalized.replace(".", "").replace(",", ".")
    elif "," in normalized:
        normalized = normalized.replace(",", ".")
    elif "." in normalized:
        dot_count = normalized.count(".")
        if dot_count > 1:
            normalized = normalized.replace(".", "")
        else:
            integer_part, decimal_part = normalized.split(".", maxsplit=1)
            if len(decimal_part) == 3:
                normalized = f"{integer_part}{decimal_part}"

    if negative:
        normalized = f"-{normalized}"

    try:
        return Decimal(normalized)
    except InvalidOperation:
        return None
