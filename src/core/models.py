from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional


@dataclass(frozen=True)
class ProcedureRecord:
    row_number: int
    numero_lote: Optional[str]
    prestador_numero: str
    protocolo_numero: str
    guia_prestador: str
    senha: str
    numero_guia_operadora: str
    data_realizacao: datetime
    tipo_glosa: str
    codigo_procedimento: str
    descricao_procedimento: str
    valor_glosado: Decimal
    justificativa: str


@dataclass(frozen=True)
class GuideGroup:
    senha: str
    records: list[ProcedureRecord]

    @property
    def first_record(self) -> ProcedureRecord:
        return self.records[0]


@dataclass(frozen=True)
class SpreadsheetSummary:
    total_rows: int
    total_guides: int
    total_procedures: int
