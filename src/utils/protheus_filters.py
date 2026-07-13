"""Filtros dinâmicos compartilhados pelos endpoints que consultam o Protheus.

Interpreta o parâmetro ``filters`` (array JSON) e converte cada objeto em uma
condição SQL segura para ser embutida no ``FromQry`` da genericQuery.
"""

import json
import re
from datetime import datetime
from typing import Any


def _to_protheus_date(value: str) -> str | None:
    """Converte uma data ``YYYY-MM-DD`` para o formato Protheus ``YYYYMMDD``.

    Retorna ``None`` se a data for inválida.
    """
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%Y%m%d")
    except ValueError:
        return None


# Operadores que comparam a coluna com um único valor (col <op> valor).
_COMPARISON_OPERATORS = {"=", "!=", ">", "<", "<=", ">="}
# Operadores de texto que usam LIKE.
_LIKE_OPERATORS = {"contem", "comeca com", "termina em"}
# Operadores que não recebem valor.
_NO_VALUE_OPERATORS = {"em branco", "nao em branco"}
# Conjunto completo de operadores aceitos.
_VALID_OPERATORS = (
    _COMPARISON_OPERATORS | _LIKE_OPERATORS | _NO_VALUE_OPERATORS | {"entre"}
)
# Tipos aceitos para o valor do filtro.
_VALID_TYPES = {"string", "number", "date"}
# Nome de coluna válido (evita injeção via nome de coluna).
_COLUMN_RE = re.compile(r"^[A-Za-z0-9_.]+$")


def _escape(value: str) -> str:
    """Escapa aspas simples para não quebrar (ou injetar) a cláusula WHERE."""
    return str(value).replace("'", "''")


def _format_scalar(value: Any, vtype: str) -> tuple[str | None, str | None]:
    """Formata um valor único como literal SQL conforme o tipo.

    Retorna ``(literal, None)`` em caso de sucesso ou ``(None, erro)``.
    """
    text = str(value).strip()
    if vtype == "number":
        try:
            float(text)
        except ValueError:
            return None, f"Valor numérico inválido: '{value}'"
        return text, None
    if vtype == "date":
        protheus_date = _to_protheus_date(text)
        if protheus_date is None:
            return None, f"Data inválida: '{value}'. Use o formato YYYY-MM-DD"
        return f"'{protheus_date}'", None
    # string (padrão): sempre entre aspas e com escape.
    return f"'{_escape(text)}'", None


def _build_filter_condition(
    column: str, operator: str, value: Any, value2: Any, vtype: str
) -> tuple[str | None, str | None]:
    """Monta a condição SQL de um filtro.

    Retorna ``(condição, None)`` em caso de sucesso ou ``(None, erro)``.
    """
    if not column:
        return None, "Filtro sem 'column'"
    if not _COLUMN_RE.match(column):
        return None, f"Nome de coluna inválido: '{column}'"
    if operator not in _VALID_OPERATORS:
        return None, (
            f"Operador inválido: '{operator}'. "
            f"Válidos: {', '.join(sorted(_VALID_OPERATORS))}"
        )
    if vtype not in _VALID_TYPES:
        return None, f"Tipo inválido: '{vtype}'. Válidos: {', '.join(sorted(_VALID_TYPES))}"

    if operator in _NO_VALUE_OPERATORS:
        if operator == "em branco":
            return f"({column} IS NULL OR RTRIM({column})='')", None
        return f"({column} IS NOT NULL AND RTRIM({column})<>'')", None

    if operator in _LIKE_OPERATORS:
        text = _escape(value)
        pattern = {
            "contem": f"%{text}%",
            "comeca com": f"{text}%",
            "termina em": f"%{text}",
        }[operator]
        return f"{column} LIKE '{pattern}'", None

    if operator == "entre":
        literal_start, err = _format_scalar(value, vtype)
        if err:
            return None, err
        literal_end, err = _format_scalar(value2, vtype)
        if err:
            return None, err
        return f"{column} BETWEEN {literal_start} AND {literal_end}", None

    # Operadores de comparação simples (=, !=, >, <, <=, >=).
    literal, err = _format_scalar(value, vtype)
    if err:
        return None, err
    return f"{column}{operator}{literal}", None


def parse_filters(raw: str) -> tuple[list[str] | None, str | None]:
    """Interpreta o parâmetro ``filters`` (JSON) e devolve as condições SQL.

    Retorna ``(condições, None)`` em caso de sucesso ou ``(None, erro)``.
    """
    try:
        parsed = json.loads(raw)
    except ValueError:
        return None, "'filters' deve ser um JSON válido (array de objetos)"

    if not isinstance(parsed, list):
        return None, "'filters' deve ser um array JSON de objetos"

    conditions: list[str] = []
    for index, item in enumerate(parsed):
        if not isinstance(item, dict):
            return None, f"Filtro na posição {index} deve ser um objeto"

        column = str(item.get("column", "")).strip()
        operator = str(item.get("operator", "")).strip()
        vtype = str(item.get("type", "string")).strip().lower() or "string"
        value = item.get("value", "")
        value2 = item.get("value2", "")

        # Conveniência: no 'entre', aceita value como lista [inicio, fim].
        if operator == "entre" and isinstance(value, list):
            if len(value) != 2:
                return None, (
                    f"Filtro na posição {index}: 'entre' exige exatamente 2 valores"
                )
            value, value2 = value[0], value[1]

        if operator in _COMPARISON_OPERATORS or operator in _LIKE_OPERATORS:
            if str(value).strip() == "":
                return None, (
                    f"Filtro na posição {index}: operador '{operator}' exige 'value'"
                )
        if operator == "entre" and (
            str(value).strip() == "" or str(value2).strip() == ""
        ):
            return None, (
                f"Filtro na posição {index}: 'entre' exige 'value' e 'value2'"
            )

        condition, err = _build_filter_condition(column, operator, value, value2, vtype)
        if err:
            return None, f"Filtro na posição {index}: {err}"
        conditions.append(condition)

    return conditions, None


def filters_openapi_param(example: str) -> dict:
    """Definição OpenAPI do parâmetro ``filters``, com o ``example`` informado."""
    return {
        "name": "filters",
        "in": "query",
        "required": False,
        "schema": {"type": "string", "example": example},
        "description": (
            "Array JSON de filtros dinâmicos. Cada objeto: `column` (obrigatório), "
            "`operator` (=, !=, >, <, <=, >=, contem, comeca com, termina em, entre, "
            "em branco, nao em branco), `value`, `value2` (para 'entre') e "
            "`type` (string | number | date). Múltiplos filtros são combinados com AND."
        ),
    }
