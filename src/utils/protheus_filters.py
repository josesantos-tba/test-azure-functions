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

# Referência (Markdown) de todos os operadores e tipos aceitos por um filtro.
# Fonte única usada na documentação OpenAPI dos endpoints que aceitam filtros
# (query-json, export-csv, table-count) e do filtro_obrigatorio (table-config).
# Mantida junto das definições acima para não sair de sincronia.
FILTERS_REFERENCE_MD = (
    "Cada filtro é um objeto com:\n"
    "- `column` (**obrigatório**): nome da coluna (ex: `E5_VALOR`).\n"
    "- `operator` (**obrigatório**): um dos operadores abaixo.\n"
    "- `value`: valor comparado (dispensado em `is_blank`/`is_not_blank`).\n"
    "- `value2`: segundo valor, usado apenas no operador `between` "
    "(ou informe `value` como lista `[inicio, fim]`).\n"
    "- `type`: `string` (padrão), `number` ou `date`.\n\n"
    "**Operadores (`operator`):**\n\n"
    "| Operador | Grupo | Efeito | Valor |\n"
    "|---|---|---|---|\n"
    "| `=` | comparação | igual a | `value` |\n"
    "| `!=` | comparação | diferente de | `value` |\n"
    "| `>` | comparação | maior que | `value` |\n"
    "| `<` | comparação | menor que | `value` |\n"
    "| `>=` | comparação | maior ou igual a | `value` |\n"
    "| `<=` | comparação | menor ou igual a | `value` |\n"
    "| `contains` | texto (LIKE) | contém o texto (`%valor%`) | `value` |\n"
    "| `starts_with` | texto (LIKE) | começa com o texto (`valor%`) | `value` |\n"
    "| `ends_with` | texto (LIKE) | termina com o texto (`%valor`) | `value` |\n"
    "| `between` | intervalo | entre dois valores (`BETWEEN`) | `value` e `value2` |\n"
    "| `is_blank` | sem valor | coluna nula ou vazia | — |\n"
    "| `is_not_blank` | sem valor | coluna preenchida | — |\n\n"
    "**Tipos (`type`):**\n\n"
    "| Tipo | Descrição |\n"
    "|---|---|\n"
    "| `string` | Padrão. Valor tratado como texto (entre aspas e com escape). |\n"
    "| `number` | Valor numérico (sem aspas); rejeita valor não-numérico. |\n"
    "| `date` | Data no formato `YYYY-MM-DD`, convertida para `YYYYMMDD` (padrão Protheus). |\n\n"
    "Múltiplos filtros são combinados com **AND**."
)


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
        if operator == "is_blank":
            return f"({column} IS NULL OR RTRIM({column})='')", None
        return f"({column} IS NOT NULL AND RTRIM({column})<>'')", None

    if operator in _LIKE_OPERATORS:
        text = _escape(value)
        pattern = {
            "contains": f"%{text}%",
            "starts_with": f"{text}%",
            "ends_with": f"%{text}",
        }[operator]
        return f"{column} LIKE '{pattern}'", None

    if operator == "between":
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
    """Interpreta o parâmetro ``filters`` (JSON *string*) e devolve as condições SQL.

    Retorna ``(condições, None)`` em caso de sucesso ou ``(None, erro)``.
    """
    try:
        parsed = json.loads(raw)
    except ValueError:
        return None, "'filters' deve ser um JSON válido (array de objetos)"

    return build_conditions(parsed)


def build_conditions(parsed: Any) -> tuple[list[str] | None, str | None]:
    """Converte uma lista de filtros **já desserializada** em condições SQL.

    Mesma estrutura aceita em ``parse_filters`` (array de objetos com ``column``,
    ``operator``, ``value``, ``value2`` e ``type``), útil quando os filtros vêm de
    uma fonte que já é uma lista Python (ex.: arquivo de configuração), não de uma
    string JSON. Retorna ``(condições, None)`` ou ``(None, erro)``.
    """
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

        # Conveniência: no 'between', aceita value como lista [inicio, fim].
        if operator == "between" and isinstance(value, list):
            if len(value) != 2:
                return None, (
                    f"Filtro na posição {index}: 'between' exige exatamente 2 valores"
                )
            value, value2 = value[0], value[1]

        if operator in _COMPARISON_OPERATORS or operator in _LIKE_OPERATORS:
            if str(value).strip() == "":
                return None, (
                    f"Filtro na posição {index}: operador '{operator}' exige 'value'"
                )
        if operator == "between" and (
            str(value).strip() == "" or str(value2).strip() == ""
        ):
            return None, (
                f"Filtro na posição {index}: 'between' exige 'value' e 'value2'"
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
        "description": "Array JSON de filtros dinâmicos.\n\n" + FILTERS_REFERENCE_MD,
    }
