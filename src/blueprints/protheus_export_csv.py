import csv
import io
import json
import logging
import os
import re

import azure.functions as func
import requests

from azure_functions_openapi import openapi

from src.utils.auth import require_roles
from src.utils.protheus_filters import filters_openapi_param, parse_filters

bp = func.Blueprint()

_PROTHEUS_BASE_URL = os.environ.get("PROTHEUS_BASE_URL", "")
_PROTHEUS_AUTH = (
    os.environ.get("PROTHEUS_USER", ""),
    os.environ.get("PROTHEUS_PASSWORD", ""),
)

# Máximo de linhas exportadas por requisição.
_MAX_ROWS = 5000

# Alias de tabela válido.
_TABLE_RE = re.compile(r"^[A-Za-z0-9_]+$")

_ERROR_SCHEMA = {
    "type": "object",
    "properties": {"error": {"type": "string", "example": "Mensagem de erro"}},
    "required": ["error"],
}


def _json_error(message: str, status: int) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps({"error": message}),
        status_code=status,
        mimetype="application/json",
    )


@openapi(
    summary="Exporta uma tabela do Protheus em CSV (até 30 mil linhas)",
    description=(
        "Exporta os registros de uma tabela do Protheus em **CSV**, com no máximo "
        f"**{_MAX_ROWS}** linhas por requisição. Aceita os mesmos parâmetros do "
        "endpoint `query-json` (`table`, `fields`, `filters`), porém **sem paginação** "
        "(`recno`/`pagesize`): o resultado vem inteiro em uma única resposta, na ordem "
        "natural retornada pelo banco de dados.\n\n"
        "A tabela é informada pelo **alias** (ex: `CTK`, `SB1`), que o próprio "
        "Protheus resolve para o nome físico via SX2.\n\n"
        "Internamente a **genericQuery** é chamada uma única vez com os parâmetros "
        f"**nativos** dela (`tables`, `fields`, `where`, `pagesize={_MAX_ROWS}`), sem "
        "`FromQry` — o corte de linhas é feito pelo próprio `pagesize` e os filtros "
        "dinâmicos viram o parâmetro `where`; os filtros de filial e de registros "
        "deletados são os automáticos da API (`FilialFilter`/`DeletedFilter`):\n"
        "```\n"
        "tables=CTK\n"
        "fields=CTK_FILIAL,CTK_CODFOR,CTK_CODCLI,CTK_SEQUEN\n"
        f"pagesize={_MAX_ROWS}\n"
        "where=CTK_FILIAL = 'TBA'\n"
        "FilialFilter=false\n"
        "```\n\n"
        "O CSV é retornado com separador vírgula, codificação UTF-8 (com BOM, para "
        "abrir corretamente no Excel) e cabeçalho com as colunas de `fields`, na ordem "
        "informada. O header `X-Row-Count` traz a quantidade de linhas de dados; se "
        f"vier igual a `limit` (padrão {_MAX_ROWS}), pode haver registros não "
        "exportados — use o endpoint `table-count` (com os mesmos `filters`) para "
        "saber o total.\n\n"
        "Use `limit` (opcional) para exportar menos linhas que o máximo.\n\n"
        "Exemplo de chamada:\n"
        "```\n"
        "GET /api/export-csv"
        "?table=SE5"
        "&fields=E5_FILIAL,E5_NUMERO,E5_DATA,E5_VALOR"
        "&filters=" + '[{"column":"E5_DATA","operator":"entre",'
        '"value":["2025-01-01","2025-01-31"],"type":"date"}]'
        "\n```"
    ),
    tags=["Protheus"],
    method="get",
    parameters=[
        {
            "name": "table",
            "in": "query",
            "required": True,
            "schema": {"type": "string", "example": "SE5"},
            "description": "Alias da tabela no Protheus (ex: SE5, SA1, SB1)",
        },
        {
            "name": "fields",
            "in": "query",
            "required": True,
            "schema": {"type": "string", "example": "E5_FILIAL,E5_NUMERO,E5_DATA,E5_VALOR"},
            "description": "Colunas desejadas separadas por vírgula (na ordem do CSV)",
        },
        filters_openapi_param(
            '[{"column":"E5_VALOR","operator":">=","value":1000,"type":"number"},'
            '{"column":"E5_NUMERO","operator":"comeca com","value":"0001"}]'
        ),
        {
            "name": "limit",
            "in": "query",
            "required": False,
            "schema": {
                "type": "integer",
                "default": _MAX_ROWS,
                "maximum": _MAX_ROWS,
                "example": 5000,
            },
            "description": f"Quantidade máxima de linhas exportadas (máx. {_MAX_ROWS})",
        },
    ],
    response={
        200: {
            "description": "CSV gerado com sucesso",
            "content": {
                "text/csv": {
                    "schema": {"type": "string"},
                    "example": (
                        "E5_FILIAL,E5_NUMERO,E5_DATA,E5_VALOR\r\n"
                        "01,000001,2025-01-15,1500.0\r\n"
                    ),
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
    operation_id="exportCsv",
)
@bp.route(route="export-csv", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
@require_roles("Tables.Read")
def export_csv(req: func.HttpRequest) -> func.HttpResponse:

    table = req.params.get("table", "").strip().upper()
    fields = req.params.get("fields", "").strip()
    filters_raw = req.params.get("filters", "").strip()
    limit_raw = req.params.get("limit", "").strip()

    missing = [p for p, v in [("table", table), ("fields", fields)] if not v]
    if missing:
        return _json_error(
            f"Parâmetros obrigatórios ausentes: {', '.join(missing)}", 400
        )

    if not _TABLE_RE.match(table):
        return _json_error(f"Alias de tabela inválido: '{table}'", 400)

    if limit_raw:
        try:
            limit = int(limit_raw)
        except ValueError:
            limit = 0
        if limit < 1:
            return _json_error("'limit' deve ser um inteiro >= 1", 400)
        limit = min(limit, _MAX_ROWS)
    else:
        limit = _MAX_ROWS

    where_parts: list[str] = []

    if filters_raw:
        conditions, err = parse_filters(filters_raw)
        if err:
            return _json_error(err, 400)
        where_parts.extend(conditions)

    # Parâmetros nativos da genericQuery: o corte de linhas fica por conta do
    # 'pagesize' e os filtros dinâmicos vão no 'where'. O filtro de deleção é
    # o automático da API (DeletedFilter, ligado por padrão).
    headers = {
        "tables": table,
        "fields": fields,
        "pagesize": str(limit),
        "FilialFilter": "false",
    }
    if where_parts:
        headers["where"] = " AND ".join(where_parts)

    try:
        resp = requests.get(
            f"{_PROTHEUS_BASE_URL}/api/framework/v1/genericQuery",
            auth=_PROTHEUS_AUTH if all(_PROTHEUS_AUTH) else None,
            timeout=60 * 10,
            headers=headers,
        )
    except requests.RequestException as exc:
        logging.error("Protheus exportCsv failed: %s", exc)
        return _json_error("Falha ao conectar à API do Protheus", 502)

    if resp.status_code >= 400:
        # Causa comum de 500 no Protheus: coluna inexistente em 'filters'
        # (em 'fields' o Protheus apenas ignora a coluna).
        logging.error(
            "Protheus exportCsv erro HTTP %s: %.500s", resp.status_code, resp.text
        )
        return _json_error(
            f"Protheus retornou erro HTTP {resp.status_code}. "
            "Verifique se as colunas usadas em 'filters' existem na tabela.",
            502,
        )

    try:
        data = resp.json()
    except ValueError:
        logging.error(
            "Protheus exportCsv retornou resposta não-JSON (status=%s): %.500s",
            resp.status_code,
            resp.text,
        )
        return _json_error(
            "Resposta inválida do Protheus (não-JSON). "
            "Verifique os campos e o tamanho da consulta.",
            502,
        )

    items = data.get("items", [])[:limit]

    # Cabeçalho na ordem de 'fields'; os valores vêm do JSON do Protheus,
    # cujas chaves são os nomes das colunas em minúsculas. Colunas protegidas
    # (não retornadas) saem vazias.
    columns = [c.strip() for c in fields.split(",") if c.strip()]
    keys = [c.lower() for c in columns]

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(columns)
    for item in items:
        writer.writerow(
            ["" if item.get(k) is None else item.get(k) for k in keys]
        )

    # BOM (utf-8-sig) para o Excel reconhecer a codificação UTF-8.
    return func.HttpResponse(
        buffer.getvalue().encode("utf-8-sig"),
        status_code=200,
        mimetype="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{table}.csv"',
            "X-Row-Count": str(len(items)),
        },
    )
