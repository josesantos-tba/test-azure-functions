"""Filtros dinâmicos para os endpoints que consultam o Databricks.

Interpreta o parâmetro ``filters`` (array JSON, mesmo contrato do Protheus) e
converte cada objeto em uma condição SQL **parametrizada**: o fragmento usa
marcadores ``?`` e os valores são devolvidos separadamente, para serem passados
como *bind parameters* ao Databricks (evita injeção de SQL).
"""

import json
import re
from datetime import datetime
from typing import Any

# O contrato de filtros (operadores/tipos) é o mesmo do Protheus; reutilizamos a
# referência única para a documentação não divergir entre os dois módulos.
from src.utils.protheus_filters import FILTERS_REFERENCE_MD

# Operadores que comparam a coluna com um único valor (col <op> ?).
_COMPARISON_OPERATORS = {"=", "!=", ">", "<", "<=", ">="}
# Operadores de texto que usam LIKE.
_LIKE_OPERATORS = {"contains", "starts_with", "ends_with"}
# Operadores que não recebem valor.
_NO_VALUE_OPERATORS = {"is_blank", "is_not_blank"}
# Conjunto completo de operadores aceitos.
_VALID_OPERATORS = (
    _COMPARISON_OPERATORS | _LIKE_OPERATORS | _NO_VALUE_OPERATORS | {"between"}
)
# Tipos aceitos para o valor do filtro.
_VALID_TYPES = {"string", "number", "date"}
# Nome de coluna válido (evita injeção via nome de coluna).
_COLUMN_RE = re.compile(r"^[A-Za-z0-9_.]+$")


def _coerce_scalar(value: Any, vtype: str) -> tuple[Any, str | None]:
    """Converte um valor único para o tipo Python adequado ao *bind*.

    Retorna ``(valor, None)`` em caso de sucesso ou ``(None, erro)``.
    """
    text = str(value).strip()
    if vtype == "number":
        try:
            number = float(text)
        except ValueError:
            return None, f"Valor numérico inválido: '{value}'"
        # Mantém inteiro quando aplicável (evita 1000 -> 1000.0).
        return (int(number) if number.is_integer() else number), None
    if vtype == "date":
        try:
            datetime.strptime(text, "%Y-%m-%d")
        except ValueError:
            return None, f"Data inválida: '{value}'. Use o formato YYYY-MM-DD"
        return text, None
    # string (padrão).
    return text, None


def _build_filter_condition(
    column: str, operator: str, value: Any, value2: Any, vtype: str
) -> tuple[str | None, list[Any], str | None]:
    """Monta ``(condição, params, None)`` de um filtro ou ``(None, [], erro)``."""
    if not column:
        return None, [], "Filtro sem 'column'"
    if not _COLUMN_RE.match(column):
        return None, [], f"Nome de coluna inválido: '{column}'"
    if operator not in _VALID_OPERATORS:
        return None, [], (
            f"Operador inválido: '{operator}'. "
            f"Válidos: {', '.join(sorted(_VALID_OPERATORS))}"
        )
    if vtype not in _VALID_TYPES:
        return None, [], (
            f"Tipo inválido: '{vtype}'. Válidos: {', '.join(sorted(_VALID_TYPES))}"
        )

    if operator in _NO_VALUE_OPERATORS:
        if operator == "is_blank":
            return f"({column} IS NULL OR trim(cast({column} AS string)) = '')", [], None
        return f"({column} IS NOT NULL AND trim(cast({column} AS string)) <> '')", [], None

    if operator in _LIKE_OPERATORS:
        text = str(value)
        pattern = {
            "contains": f"%{text}%",
            "starts_with": f"{text}%",
            "ends_with": f"%{text}",
        }[operator]
        return f"{column} LIKE ?", [pattern], None

    if operator == "between":
        start, err = _coerce_scalar(value, vtype)
        if err:
            return None, [], err
        end, err = _coerce_scalar(value2, vtype)
        if err:
            return None, [], err
        return f"{column} BETWEEN ? AND ?", [start, end], None

    # Operadores de comparação simples (=, !=, >, <, <=, >=).
    scalar, err = _coerce_scalar(value, vtype)
    if err:
        return None, [], err
    return f"{column} {operator} ?", [scalar], None


def parse_filters(raw: str) -> tuple[list[str] | None, list[Any], str | None]:
    """Interpreta o parâmetro ``filters`` (JSON).

    Retorna ``(condições, params, None)`` em caso de sucesso — ``condições`` são
    fragmentos SQL com ``?`` e ``params`` os valores na mesma ordem — ou
    ``(None, [], erro)`` em caso de falha.
    """
    try:
        parsed = json.loads(raw)
    except ValueError:
        return None, [], "'filters' deve ser um JSON válido (array de objetos)"

    if not isinstance(parsed, list):
        return None, [], "'filters' deve ser um array JSON de objetos"

    conditions: list[str] = []
    params: list[Any] = []
    for index, item in enumerate(parsed):
        if not isinstance(item, dict):
            return None, [], f"Filtro na posição {index} deve ser um objeto"

        column = str(item.get("column", "")).strip()
        operator = str(item.get("operator", "")).strip()
        vtype = str(item.get("type", "string")).strip().lower() or "string"
        value = item.get("value", "")
        value2 = item.get("value2", "")

        # Conveniência: no 'between', aceita value como lista [inicio, fim].
        if operator == "between" and isinstance(value, list):
            if len(value) != 2:
                return None, [], (
                    f"Filtro na posição {index}: 'between' exige exatamente 2 valores"
                )
            value, value2 = value[0], value[1]

        if operator in _COMPARISON_OPERATORS or operator in _LIKE_OPERATORS:
            if str(value).strip() == "":
                return None, [], (
                    f"Filtro na posição {index}: operador '{operator}' exige 'value'"
                )
        if operator == "between" and (
            str(value).strip() == "" or str(value2).strip() == ""
        ):
            return None, [], (
                f"Filtro na posição {index}: 'between' exige 'value' e 'value2'"
            )

        condition, cond_params, err = _build_filter_condition(
            column, operator, value, value2, vtype
        )
        if err:
            return None, [], f"Filtro na posição {index}: {err}"
        conditions.append(condition)
        params.extend(cond_params)

    return conditions, params, None


def filters_openapi_param(example: str) -> dict:
    """Definição OpenAPI do parâmetro ``filters``, com o ``example`` informado."""
    return {
        "name": "filters",
        "in": "query",
        "required": False,
        "schema": {"type": "string", "example": example},
        "description": "Array JSON de filtros dinâmicos.\n\n" + FILTERS_REFERENCE_MD,
    }
