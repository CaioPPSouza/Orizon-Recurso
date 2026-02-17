from __future__ import annotations

from decimal import Decimal

import pandas as pd
import pytest

from src.core import SpreadsheetValidationError, load_and_group_spreadsheet


def test_load_and_group_spreadsheet_groups_by_password(tmp_path):
    file_path = tmp_path / "dados.xlsx"
    dataframe = pd.DataFrame(
        [
            {
                "PrestadorNúmero": "1001",
                "ProtocoloNúmero": "PROTO-2",
                "Guia Prestador": "GP-1",
                "Senha": "A",
                "Número Guia Operadora": "GO-1",
                "Data Realização": "02/01/2025",
                "Tipo Glosa": "G1",
                "Código Procedimento": "PROC-2",
                "Descrição Procedimento": "Procedimento 2",
                "Valor Glosado": "10,00",
                "Justificativa": "Teste",
            },
            {
                "PrestadorNúmero": "1001",
                "ProtocoloNúmero": "PROTO-1",
                "Guia Prestador": "GP-1",
                "Senha": "A",
                "Número Guia Operadora": "GO-1",
                "Data Realização": "01/01/2025",
                "Tipo Glosa": "G1",
                "Código Procedimento": "PROC-1",
                "Descrição Procedimento": "Procedimento 1",
                "Valor Glosado": "20,00",
                "Justificativa": "Teste",
            },
            {
                "PrestadorNúmero": "1002",
                "ProtocoloNúmero": "PROTO-3",
                "Guia Prestador": "GP-2",
                "Senha": "B",
                "Número Guia Operadora": "GO-2",
                "Data Realização": "03/01/2025",
                "Tipo Glosa": "G2",
                "Código Procedimento": "PROC-3",
                "Descrição Procedimento": "Procedimento 3",
                "Valor Glosado": "1.234,56",
                "Justificativa": "Teste 2",
            },
        ]
    )
    dataframe.to_excel(file_path, index=False, sheet_name="Planilha Analisada")

    data = load_and_group_spreadsheet(file_path)

    assert data.summary.total_rows == 3
    assert data.summary.total_guides == 2
    assert data.summary.total_procedures == 3

    assert data.groups[0].senha == "A"
    assert [record.codigo_procedimento for record in data.groups[0].records] == ["PROC-1", "PROC-2"]
    assert data.groups[1].senha == "B"
    assert data.groups[1].records[0].valor_glosado == Decimal("1234.56")


def test_load_and_group_spreadsheet_validates_required_columns(tmp_path):
    file_path = tmp_path / "dados_invalidos.xlsx"
    dataframe = pd.DataFrame([{"Senha": "A"}])
    dataframe.to_excel(file_path, index=False, sheet_name="Planilha Analisada")

    with pytest.raises(SpreadsheetValidationError) as exc:
        load_and_group_spreadsheet(file_path)

    assert "Colunas obrigatorias ausentes" in str(exc.value)


def test_load_and_group_spreadsheet_parses_monetary_formats_with_dot(tmp_path):
    file_path = tmp_path / "dados_valor.xlsx"
    dataframe = pd.DataFrame(
        [
            {
                "PrestadorNúmero": "1001",
                "ProtocoloNúmero": "PROTO-1",
                "Guia Prestador": "GP-1",
                "Senha": "A",
                "Número Guia Operadora": "GO-1",
                "Data Realização": "01/01/2025",
                "Tipo Glosa": "G1",
                "Código Procedimento": "PROC-1",
                "Descrição Procedimento": "Procedimento 1",
                "Valor Glosado": "1.234",
                "Justificativa": "Teste",
            },
            {
                "PrestadorNúmero": "1001",
                "ProtocoloNúmero": "PROTO-2",
                "Guia Prestador": "GP-1",
                "Senha": "A",
                "Número Guia Operadora": "GO-1",
                "Data Realização": "02/01/2025",
                "Tipo Glosa": "G1",
                "Código Procedimento": "PROC-2",
                "Descrição Procedimento": "Procedimento 2",
                "Valor Glosado": "10.50",
                "Justificativa": "Teste",
            },
        ]
    )
    dataframe.to_excel(file_path, index=False, sheet_name="Planilha Analisada")

    data = load_and_group_spreadsheet(file_path)
    values = [record.valor_glosado for record in data.groups[0].records]

    assert values == [Decimal("1234"), Decimal("10.50")]
