import json
import logging
import os
import re
from datetime import datetime
from typing import Any

import azure.functions as func
import requests
from pydantic import BaseModel, Field

from azure_functions_openapi import openapi

from src.utils.auth import require_roles
from src.utils.openapi import inline_refs

bp = func.Blueprint()

_PROTHEUS_BASE_URL = os.environ.get("PROTHEUS_BASE_URL", "")
_PROTHEUS_AUTH = (
    os.environ.get("PROTHEUS_USER", ""),
    os.environ.get("PROTHEUS_PASSWORD", ""),
)

_DEFAULT_PAGESIZE = 100
_MAX_PAGESIZE = 10000

_ERROR_SCHEMA = {
    "type": "object",
    "properties": {"error": {"type": "string", "example": "Mensagem de erro"}},
    "required": ["error"],
}


class QueryResponse(BaseModel):
    total: int = Field(
        description="Total de registros que atendem à consulta (todas as páginas)."
    )
    has_next: bool = Field(description="Indica se há uma próxima página.")
    page: int = Field(description="Página retornada.")
    pagesize: int = Field(description="Registros por página.")
    items: list[dict[str, Any]] = Field(
        description=(
            "Registros retornados. Cada item é um objeto dinâmico cujas chaves "
            "correspondem às colunas pedidas em `fields` (em minúsculas) e os valores "
            "podem ser de qualquer tipo (string, número, etc.) conforme o campo no Protheus."
        )
    )


def _parse_int(value: str, default: int) -> int | None:
    """Retorna o inteiro contido em ``value`` ou ``default`` se vazio; ``None`` se inválido."""
    value = value.strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return None


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


def _parse_filters(raw: str) -> tuple[list[str] | None, str | None]:
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


@openapi(
    summary="Consulta em tabela do Protheus (JSON) com paginação e filtros dinâmicos",
    description=(
        "Executa uma consulta parametrizável em qualquer tabela do Protheus via **genericQuery** "
        "e retorna os dados em **JSON**.\n\n"
        "Suporta paginação (`page`/`pagesize`) e **filtros dinâmicos** via `filters`, "
        "onde é possível informar N condições — inclusive filtro por data, que é apenas "
        "um filtro com `type: date`.\n\n"
        "### Filtros dinâmicos (`filters`)\n"
        "`filters` é um **array JSON** de objetos. Cada objeto aceita:\n"
        "- `column` (obrigatório): nome da coluna (ex: `E5_VALOR`).\n"
        "- `operator` (obrigatório): um dos operadores abaixo.\n"
        "- `value`: valor comparado (dispensado em `em branco`/`nao em branco`).\n"
        "- `value2`: segundo valor, usado apenas no operador `entre` "
        "(ou informe `value` como lista `[inicio, fim]`).\n"
        "- `type` (opcional): `string` (padrão), `number` ou `date` (`YYYY-MM-DD`).\n\n"
        "Operadores suportados: `=`, `!=`, `>`, `<`, `<=`, `>=`, `contem`, "
        "`comeca com`, `termina em`, `entre`, `em branco`, `nao em branco`.\n\n"
        "Exemplo de chamada:\n"
        "```\n"
        "GET /api/query-json"
        "?table=SE5"
        "&fields=E5_FILIAL,E5_NUM,E5_DATA,E5_VALOR"
        "&filters=" + '[{"column":"E5_VALOR","operator":">=","value":1000,"type":"number"},'
        '{"column":"E5_NUM","operator":"comeca com","value":"0001"},'
        '{"column":"E5_DATA","operator":"entre","value":["2025-01-01","2025-01-31"],"type":"date"}]'
        + "&page=1"
        "&pagesize=50"
        "```"
    ),
    tags=["Protheus"],
    method="get",
    parameters=[
        {
            "name": "table",
            "in": "query",
            "required": True,
            "schema": {"type": "string", "example": "SE5"},
            "description": "Nome da tabela no Protheus (ex: SE5, SA1, SB1)",
        },
        {
            "name": "fields",
            "in": "query",
            "required": True,
            "schema": {"type": "string", "example": "E5_FILIAL,E5_NUM,E5_DATA,E5_VALOR"},
            "description": "Colunas desejadas separadas por vírgula",
        },
        {
            "name": "filters",
            "in": "query",
            "required": False,
            "schema": {
                "type": "string",
                "example": (
                    '[{"column":"E5_VALOR","operator":">=","value":1000,"type":"number"},'
                    '{"column":"E5_NUM","operator":"comeca com","value":"0001"}]'
                ),
            },
            "description": (
                "Array JSON de filtros dinâmicos. Cada objeto: `column` (obrigatório), "
                "`operator` (=, !=, >, <, <=, >=, contem, comeca com, termina em, entre, "
                "em branco, nao em branco), `value`, `value2` (para 'entre') e "
                "`type` (string | number | date). Múltiplos filtros são combinados com AND."
            ),
        },
        {
            "name": "page",
            "in": "query",
            "required": False,
            "schema": {"type": "integer", "default": 1, "example": 1},
            "description": "Número da página a retornar (começa em 1)",
        },
        {
            "name": "pagesize",
            "in": "query",
            "required": False,
            "schema": {"type": "integer", "default": _DEFAULT_PAGESIZE, "example": 50},
            "description": f"Quantidade de registros por página (máx. {_MAX_PAGESIZE})",
        },
    ],
    response={
        200: {
            "description": "Dados retornados com sucesso",
            "content": {
                "application/json": {
                    "schema": inline_refs(QueryResponse.model_json_schema()),
                    "example": {
                        "total": 137,
                        "has_next": True,
                        "page": 1,
                        "pagesize": 50,
                        "items": [
                            {
                                "e5_filial": "01",
                                "e5_num": "000001",
                                "e5_data": "20250115",
                                "e5_valor": 1500.0,
                            },
                        ],
                    },
                }
            },
        },
        400: {
            "description": "Parâmetros obrigatórios ausentes ou inválidos",
            "content": {
                "application/json": {
                    "schema": _ERROR_SCHEMA,
                    "example": {"error": "Parâmetros obrigatórios ausentes: table, fields"},
                }
            },
        },
        401: {
            "description": "Requisição não autenticada (token do Entra ID ausente ou inválido)",
            "content": {
                "application/json": {
                    "schema": _ERROR_SCHEMA,
                    "example": {"error": "Requisição não autenticada."},
                }
            },
        },
        403: {
            "description": "Usuário autenticado sem a role 'Tables.Read'",
            "content": {
                "application/json": {
                    "schema": _ERROR_SCHEMA,
                    "example": {
                        "error": "Acesso negado: você não tem a permissão necessária "
                        "para acessar este recurso."
                    },
                }
            },
        },
        502: {
            "description": "Falha ao conectar ou obter resposta da API do Protheus",
            "content": {
                "application/json": {
                    "schema": _ERROR_SCHEMA,
                    "example": {"error": "Falha ao conectar à API do Protheus"},
                }
            },
        },
    },
    operation_id="queryJson",
)
@bp.route(route="query-json", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
@require_roles("Tables.Read")
def query_json(req: func.HttpRequest) -> func.HttpResponse:

    table = req.params.get("table", "").strip()
    fields = req.params.get("fields", "").strip()
    filters_raw = req.params.get("filters", "").strip()

    missing = [p for p, v in [("table", table), ("fields", fields)] if not v]
    if missing:
        return func.HttpResponse(
            json.dumps({"error": f"Parâmetros obrigatórios ausentes: {', '.join(missing)}"}),
            status_code=400,
            mimetype="application/json",
        )

    page = _parse_int(req.params.get("page", ""), 1)
    pagesize = _parse_int(req.params.get("pagesize", ""), _DEFAULT_PAGESIZE)
    if page is None or pagesize is None or page < 1 or pagesize < 1:
        return func.HttpResponse(
            json.dumps({"error": "'page' e 'pagesize' devem ser inteiros >= 1"}),
            status_code=400,
            mimetype="application/json",
        )

    pagesize = min(pagesize, _MAX_PAGESIZE)

    where_parts = [f"{table}.D_E_L_E_T_=' '"]

    if filters_raw:
        conditions, err = _parse_filters(filters_raw)
        if err:
            return func.HttpResponse(
                json.dumps({"error": err}),
                status_code=400,
                mimetype="application/json",
            )
        where_parts.extend(conditions)

    query_params: dict[str, str] = {
        "tables": table,
        "fields": fields,
        "where": " AND ".join(where_parts),
        "page": str(page),
        "pagesize": str(pagesize),
    }

    headers = {
        "FilialFilter": "false"
    }

    try:
        resp = requests.get(
            f"{_PROTHEUS_BASE_URL}/api/framework/v1/genericQuery",
            params=query_params,
            auth=_PROTHEUS_AUTH if all(_PROTHEUS_AUTH) else None,
            timeout=60*10,
            headers=headers
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        logging.error("Protheus queryJson failed: %s", exc)
        return func.HttpResponse(
            json.dumps({"error": "Falha ao conectar à API do Protheus"}),
            status_code=502,
            mimetype="application/json",
        )

    try:
        data = resp.json()
    except ValueError:
        # Protheus respondeu algo que não é JSON (corpo vazio, HTML de erro, etc.).
        # Causa comum: URL longa demais (muitas colunas em 'fields') ou campo inválido.
        logging.error(
            "Protheus queryJson retornou resposta não-JSON (status=%s): %.500s",
            resp.status_code,
            resp.text,
        )
        return func.HttpResponse(
            json.dumps(
                {"error": "Resposta inválida do Protheus (não-JSON). Verifique os campos e o tamanho da consulta."}
            ),
            status_code=502,
            mimetype="application/json",
        )

    items: list[dict[str, Any]] = data.get("items", [])

    return func.HttpResponse(
        QueryResponse(
            total=int(data.get("total", 0)),
            has_next=bool(data.get("hasNext", False)),
            page=page,
            pagesize=pagesize,
            items=items,
        ).model_dump_json(),
        status_code=200,
        mimetype="application/json",
    )
