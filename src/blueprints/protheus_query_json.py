import json
import logging
import os
import re
from typing import Any

import azure.functions as func
import requests
from pydantic import BaseModel, Field

from azure_functions_openapi import openapi

from src.utils.auth import require_roles
from src.utils.openapi import inline_refs
from src.utils.protheus_filters import filters_openapi_param, parse_filters

bp = func.Blueprint()

_PROTHEUS_BASE_URL = os.environ.get("PROTHEUS_BASE_URL", "")
_PROTHEUS_AUTH = (
    os.environ.get("PROTHEUS_USER", ""),
    os.environ.get("PROTHEUS_PASSWORD", ""),
)

_DEFAULT_PAGESIZE = 100
_MAX_PAGESIZE = 10000

# Alias de tabela válido (evita injeção via nome de tabela no FromQry).
_TABLE_RE = re.compile(r"^[A-Za-z0-9_]+$")

# Sufixo padrão que converte o alias (ex: CTK) no nome físico (ex: CTK010).
_TABLE_SUFFIX = "010"

# FromQry com o WHERE (cursor de recno, deleção e filtros dinâmicos) embutido
# na subquery, sem usar o parâmetro 'where' do Protheus. Ordena por
# R_E_C_N_O_ DESC para os registros mais recentes virem primeiro.
_FROM_QRY_TEMPLATE = (
    "(SELECT * FROM {table} WHERE {where} "
    "ORDER BY R_E_C_N_O_ DESC FETCH NEXT {rows} ROWS ONLY) {alias}"
)

# FromQry de cursores (mesmo artifício da A10 usado no table-count): como o
# Protheus não retorna a coluna R_E_C_N_O_ nos itens, uma segunda chamada
# devolve 4 linhas, cada uma carregando um escalar em A10_PRAZO (numérico,
# sem truncamento), identificado pela tag em A10_ETAPA:
#   '1' = menor R_E_C_N_O_ da página atual (candidato a next_recno)
#   '2' = qtde de registros da janela pagesize+1 (indica se há próxima página)
#   '3' = qtde de registros acima do cursor (limitada a pagesize)
#   '4' = maior R_E_C_N_O_ da janela acima do cursor (candidato a previous_recno)
_CURSORS_FROM_QRY_TEMPLATE = (
    "(SELECT '' AS A10_FILIAL,'' AS A10_CJETAP,'1' AS A10_ETAPA,'' AS A10_ETDESC,"
    "'' AS A10_WKFLOW,"
    "(SELECT MIN(R_E_C_N_O_) FROM (SELECT R_E_C_N_O_ FROM {table} WHERE {where_page} "
    "ORDER BY R_E_C_N_O_ DESC FETCH NEXT {pagesize} ROWS ONLY)) AS A10_PRAZO,"
    "1 AS R_E_C_N_O_,0 AS R_E_C_D_E_L_,' ' AS D_E_L_E_T_ FROM DUAL"
    " UNION ALL SELECT '','','2','','',"
    "(SELECT COUNT(*) FROM (SELECT R_E_C_N_O_ FROM {table} WHERE {where_page} "
    "ORDER BY R_E_C_N_O_ DESC FETCH NEXT {rows_next} ROWS ONLY)),"
    "2,0,' ' FROM DUAL"
    " UNION ALL SELECT '','','3','','',"
    "(SELECT COUNT(*) FROM (SELECT R_E_C_N_O_ FROM {table} WHERE {where_above} "
    "ORDER BY R_E_C_N_O_ ASC FETCH NEXT {pagesize} ROWS ONLY)),"
    "3,0,' ' FROM DUAL"
    " UNION ALL SELECT '','','4','','',"
    "(SELECT MAX(R_E_C_N_O_) FROM (SELECT R_E_C_N_O_ FROM {table} WHERE {where_above} "
    "ORDER BY R_E_C_N_O_ ASC FETCH NEXT {pagesize} ROWS ONLY)),"
    "4,0,' ' FROM DUAL) A10"
)

_ERROR_SCHEMA = {
    "type": "object",
    "properties": {"error": {"type": "string", "example": "Mensagem de erro"}},
    "required": ["error"],
}


class QueryResponse(BaseModel):
    pagesize: int = Field(description="Registros por página.")
    previous_recno: int | None = Field(
        description=(
            "Valor de `recno` para buscar a página anterior (registros mais recentes). "
            "`0` indica que a anterior é a primeira página; `null` indica que esta já "
            "é a primeira página."
        )
    )
    next_recno: int | None = Field(
        description=(
            "Valor de `recno` para buscar a próxima página (registros mais antigos). "
            "`null` quando não há próxima página."
        )
    )
    items: list[dict[str, Any]] = Field(
        description=(
            "Registros retornados, ordenados do mais recente para o mais antigo "
            "(R_E_C_N_O_ decrescente). Cada item é um objeto dinâmico cujas chaves "
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


def _to_int(value: Any) -> int | None:
    """Converte um valor vindo do Protheus (número, string ou vazio) em int, ou ``None``."""
    try:
        text = str(value).strip()
        if not text:
            return None
        return int(float(text))
    except (TypeError, ValueError):
        return None


def _compute_cursors(
    recno: int, pagesize: int, rows: list[dict[str, Any]]
) -> tuple[int | None, int | None]:
    """Calcula ``(previous_recno, next_recno)`` a partir das linhas de cursores.

    ``rows`` são os items da chamada de cursores: cada linha tem a tag em
    ``a10_etapa`` e o valor em ``a10_prazo`` (ver _CURSORS_FROM_QRY_TEMPLATE).
    """
    values: dict[str, Any] = {}
    for row in rows:
        values[str(row.get("a10_etapa", "")).strip()] = row.get("a10_prazo")

    page_min = _to_int(values.get("1"))
    window_count = _to_int(values.get("2")) or 0
    count_above = _to_int(values.get("3")) or 0
    above_max = _to_int(values.get("4"))

    # Há próxima página se a janela pagesize+1 veio cheia; o cursor é o menor
    # recno da página atual (a próxima página são os registros abaixo dele).
    next_recno = page_min if window_count == pagesize + 1 else None

    if recno == 0:
        # Primeira página: não há anterior.
        previous_recno = None
    elif count_above >= pagesize and above_max is not None:
        # O cursor da página anterior é o pagesize-ésimo recno acima do atual.
        previous_recno = above_max
    else:
        # Menos de pagesize registros acima: a anterior é a primeira página.
        previous_recno = 0
    return previous_recno, next_recno


def _protheus_query(
    headers: dict[str, str],
) -> tuple[dict[str, Any] | None, func.HttpResponse | None]:
    """Chama a genericQuery com os ``headers`` informados.

    Retorna ``(json, None)`` em caso de sucesso ou ``(None, resposta_de_erro)``.
    """
    try:
        resp = requests.get(
            f"{_PROTHEUS_BASE_URL}/api/framework/v1/genericQuery",
            auth=_PROTHEUS_AUTH if all(_PROTHEUS_AUTH) else None,
            timeout=60 * 10,
            headers=headers,
        )
    except requests.RequestException as exc:
        logging.error("Protheus queryJson failed: %s", exc)
        return None, func.HttpResponse(
            json.dumps({"error": "Falha ao conectar à API do Protheus"}),
            status_code=502,
            mimetype="application/json",
        )

    if resp.status_code >= 400:
        # Causa comum de 500 no Protheus: coluna inexistente em 'filters'
        # (em 'fields' o Protheus apenas ignora a coluna).
        logging.error(
            "Protheus queryJson erro HTTP %s: %.500s", resp.status_code, resp.text
        )
        return None, func.HttpResponse(
            json.dumps(
                {
                    "error": (
                        f"Protheus retornou erro HTTP {resp.status_code}. "
                        "Verifique se as colunas usadas em 'filters' existem na tabela."
                    )
                }
            ),
            status_code=502,
            mimetype="application/json",
        )

    try:
        return resp.json(), None
    except ValueError:
        logging.error(
            "Protheus queryJson retornou resposta não-JSON (status=%s): %.500s",
            resp.status_code,
            resp.text,
        )
        return None, func.HttpResponse(
            json.dumps(
                {"error": "Resposta inválida do Protheus (não-JSON). Verifique os campos e o tamanho da consulta."}
            ),
            status_code=502,
            mimetype="application/json",
        )


@openapi(
    summary="Consulta em tabela do Protheus (JSON) com paginação por recno e filtros dinâmicos",
    description=(
        "Executa uma consulta parametrizável em qualquer tabela do Protheus via **genericQuery** "
        "e retorna os dados em **JSON**.\n\n"
        "A tabela é informada pelo **alias** (ex: `CTK`, `SB1`); o nome físico é montado "
        f"com o sufixo `{_TABLE_SUFFIX}` (ex: `CTK` → `CTK{_TABLE_SUFFIX}`).\n\n"
        "Internamente a genericQuery é chamada com os parâmetros no **header** — o WHERE "
        "(cursor de recno, deleção e filtros) fica embutido no `FromQry`, sem usar o "
        "parâmetro `where` do Protheus. Exemplo de segunda página:\n"
        "```\n"
        "tables=CTK\n"
        "fields=CTK_FILIAL,CTK_CODFOR,CTK_CODCLI,CTK_SEQUEN\n"
        "pagesize=50\n"
        f"FromQry=(SELECT * FROM CTK{_TABLE_SUFFIX} WHERE R_E_C_N_O_ < 21410710 AND "
        "D_E_L_E_T_ <> '*' ORDER BY R_E_C_N_O_ DESC FETCH NEXT 50 ROWS ONLY) CTK\n"
        "FilialFilter=false\n"
        "```\n\n"
        "### Paginação (recno + pagesize)\n"
        "Os registros vêm ordenados por `R_E_C_N_O_` **decrescente** — os mais recentes "
        "aparecem primeiro. A primeira página usa `recno=0` (padrão); as demais usam os "
        "cursores devolvidos na resposta:\n"
        "- `next_recno`: informe em `recno` para buscar a **próxima** página (registros "
        "mais antigos). `null` quando não há próxima página.\n"
        "- `previous_recno`: informe em `recno` para **voltar** à página anterior "
        "(registros mais recentes). `0` indica que a anterior é a primeira página; "
        "`null` indica que esta já é a primeira.\n\n"
        "Como o Protheus não retorna a coluna `R_E_C_N_O_` nos itens, os cursores são "
        "calculados em uma segunda chamada à genericQuery com o mesmo artifício da "
        "tabela `A10` usado no `table-count`. Não há retorno do total de registros — "
        "use o endpoint `table-count` (com os mesmos `filters`) se precisar do total.\n\n"
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
        "&fields=E5_FILIAL,E5_NUMERO,E5_DATA,E5_VALOR"
        "&filters=" + '[{"column":"E5_VALOR","operator":">=","value":1000,"type":"number"},'
        '{"column":"E5_NUMERO","operator":"comeca com","value":"0001"},'
        '{"column":"E5_DATA","operator":"entre","value":["2025-01-01","2025-01-31"],"type":"date"}]'
        + "&recno=0"
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
            "description": "Alias da tabela no Protheus (ex: SE5, SA1, SB1)",
        },
        {
            "name": "fields",
            "in": "query",
            "required": True,
            "schema": {"type": "string", "example": "E5_FILIAL,E5_NUMERO,E5_DATA,E5_VALOR"},
            "description": "Colunas desejadas separadas por vírgula",
        },
        filters_openapi_param(
            '[{"column":"E5_VALOR","operator":">=","value":1000,"type":"number"},'
            '{"column":"E5_NUMERO","operator":"comeca com","value":"0001"}]'
        ),
        {
            "name": "recno",
            "in": "query",
            "required": False,
            "schema": {"type": "integer", "default": 0, "example": 0},
            "description": (
                "Cursor de paginação: use 0 (padrão) para a primeira página e o "
                "`next_recno`/`previous_recno` da resposta anterior para navegar."
            ),
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
                        "pagesize": 50,
                        "previous_recno": 0,
                        "next_recno": 123406,
                        "items": [
                            {
                                "e5_filial": "01",
                                "e5_numero": "000001",
                                "e5_data": "2025-01-15",
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

    table = req.params.get("table", "").strip().upper()
    fields = req.params.get("fields", "").strip()
    filters_raw = req.params.get("filters", "").strip()

    missing = [p for p, v in [("table", table), ("fields", fields)] if not v]
    if missing:
        return func.HttpResponse(
            json.dumps({"error": f"Parâmetros obrigatórios ausentes: {', '.join(missing)}"}),
            status_code=400,
            mimetype="application/json",
        )

    if not _TABLE_RE.match(table):
        return func.HttpResponse(
            json.dumps({"error": f"Alias de tabela inválido: '{table}'"}),
            status_code=400,
            mimetype="application/json",
        )

    recno = _parse_int(req.params.get("recno", ""), 0)
    pagesize = _parse_int(req.params.get("pagesize", ""), _DEFAULT_PAGESIZE)
    if recno is None or recno < 0:
        return func.HttpResponse(
            json.dumps({"error": "'recno' deve ser um inteiro >= 0"}),
            status_code=400,
            mimetype="application/json",
        )
    if pagesize is None or pagesize < 1:
        return func.HttpResponse(
            json.dumps({"error": "'pagesize' deve ser um inteiro >= 1"}),
            status_code=400,
            mimetype="application/json",
        )

    pagesize = min(pagesize, _MAX_PAGESIZE)

    base_conditions = ["D_E_L_E_T_ <> '*'"]

    if filters_raw:
        conditions, err = parse_filters(filters_raw)
        if err:
            return func.HttpResponse(
                json.dumps({"error": err}),
                status_code=400,
                mimetype="application/json",
            )
        base_conditions.extend(conditions)

    physical_table = f"{table}{_TABLE_SUFFIX}"

    # Página atual: registros abaixo do cursor (ou do topo, na primeira página).
    page_bound = f"R_E_C_N_O_ < {recno}" if recno else "R_E_C_N_O_ > 0"
    where_page = " AND ".join([page_bound, *base_conditions])
    # Janela acima do cursor: usada para calcular o previous_recno.
    where_above = " AND ".join([f"R_E_C_N_O_ > {recno}", *base_conditions])

    data, error_response = _protheus_query(
        {
            "tables": table,
            "fields": fields,
            "pagesize": str(pagesize),
            "FromQry": _FROM_QRY_TEMPLATE.format(
                table=physical_table, where=where_page, rows=pagesize, alias=table
            ),
            "FilialFilter": "false",
        }
    )
    if error_response is not None:
        return error_response

    items: list[dict[str, Any]] = data.get("items", [])[:pagesize]

    if recno == 0 and len(items) < pagesize:
        # Primeira página incompleta: não existem outras páginas.
        previous_recno: int | None = None
        next_recno: int | None = None
    else:
        cursors_data, error_response = _protheus_query(
            {
                "tables": "A10",
                "fields": "A10_ETAPA,A10_PRAZO",
                "pagesize": "4",
                "FromQry": _CURSORS_FROM_QRY_TEMPLATE.format(
                    table=physical_table,
                    where_page=where_page,
                    where_above=where_above,
                    pagesize=pagesize,
                    rows_next=pagesize + 1,
                ),
                "FilialFilter": "false",
            }
        )
        if error_response is not None:
            return error_response

        cursor_rows = cursors_data.get("items", [])
        if not cursor_rows:
            logging.error("Protheus queryJson sem as linhas de cursores de paginação")
            return func.HttpResponse(
                json.dumps({"error": "Resposta do Protheus sem os cursores de paginação."}),
                status_code=502,
                mimetype="application/json",
            )

        previous_recno, next_recno = _compute_cursors(recno, pagesize, cursor_rows)

    return func.HttpResponse(
        QueryResponse(
            pagesize=pagesize,
            previous_recno=previous_recno,
            next_recno=next_recno,
            items=items,
        ).model_dump_json(),
        status_code=200,
        mimetype="application/json",
    )
