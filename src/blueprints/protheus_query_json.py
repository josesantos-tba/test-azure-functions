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

# FromQry único (página + cursores em uma só chamada à genericQuery), com o
# WHERE (cursor de recno, deleção e filtros dinâmicos) embutido na subquery,
# sem usar o parâmetro 'where' do Protheus. Ordena por R_E_C_N_O_ DESC para
# os registros mais recentes virem primeiro e busca pagesize+1 linhas para
# saber se há próxima página.
#
# Como o Protheus não retorna a coluna R_E_C_N_O_ nos itens, cada linha
# carrega o próprio R_E_C_N_O_ numa coluna numérica "emprestada" de outra
# tabela padrão (ex: A10_PRAZO — numérica, sem truncamento) e o escalar do
# previous_recno em outra (ex: E5_VALOR). A genericQuery aceita múltiplos
# aliases em 'tables' separados por vírgula, o que permite pedir em 'fields'
# colunas dessas tabelas junto com as da tabela consultada.
_FROM_QRY_TEMPLATE = (
    "(SELECT T.*, T.R_E_C_N_O_ AS {recno_col}, {prev_expr} AS {prev_col} "
    "FROM {table} T WHERE {where} "
    "ORDER BY T.R_E_C_N_O_ DESC FETCH NEXT {rows} ROWS ONLY) {alias}"
)

# Escalar do previous_recno: o pagesize-ésimo R_E_C_N_O_ acima do cursor.
# Não retorna linha (vira NULL na coluna) quando há menos de pagesize
# registros acima — nesse caso a página anterior é a primeira (cursor 0).
_PREV_EXPR_TEMPLATE = (
    "(SELECT MAX(R_E_C_N_O_) FROM ("
    "SELECT R_E_C_N_O_ FROM {table} WHERE {where_above} "
    "ORDER BY R_E_C_N_O_ ASC FETCH NEXT {pagesize} ROWS ONLY) "
    "HAVING COUNT(*) >= {pagesize})"
)

# Colunas numéricas de tabelas padrão do Protheus usadas como "carona" para
# o R_E_C_N_O_ de cada linha e para o escalar do previous_recno. São usadas
# as duas primeiras cuja tabela difere da consultada, para não colidir com
# as colunas reais quando a própria A10/SE5 for consultada.
_SENTINEL_COLUMNS = [("A10", "A10_PRAZO"), ("SE5", "E5_VALOR"), ("SE2", "E2_VALOR")]

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
        "Internamente a genericQuery é chamada **uma única vez** com os parâmetros no "
        "**header** — o WHERE (cursor de recno, deleção e filtros) fica embutido no "
        "`FromQry`, sem usar o parâmetro `where` do Protheus. Como a genericQuery aceita "
        "múltiplos aliases em `tables`, dados e cursores de paginação vêm na mesma "
        "resposta: cada linha carrega o próprio `R_E_C_N_O_` na coluna emprestada "
        "`A10_PRAZO` e o cursor da página anterior em `E5_VALOR` (colunas numéricas de "
        "tabelas padrão, removidas dos itens antes da resposta). Exemplo de segunda página:\n"
        "```\n"
        "tables=CTK,A10,SE5\n"
        "fields=CTK_FILIAL,CTK_CODFOR,CTK_CODCLI,CTK_SEQUEN,A10_PRAZO,E5_VALOR\n"
        "pagesize=51\n"
        f"FromQry=(SELECT T.*, T.R_E_C_N_O_ AS A10_PRAZO, (SELECT MAX(R_E_C_N_O_) FROM "
        f"(SELECT R_E_C_N_O_ FROM CTK{_TABLE_SUFFIX} WHERE R_E_C_N_O_ > 21410710 AND "
        "D_E_L_E_T_ <> '*' ORDER BY R_E_C_N_O_ ASC FETCH NEXT 50 ROWS ONLY) HAVING "
        f"COUNT(*) >= 50) AS E5_VALOR FROM CTK{_TABLE_SUFFIX} T WHERE "
        "R_E_C_N_O_ < 21410710 AND D_E_L_E_T_ <> '*' ORDER BY T.R_E_C_N_O_ DESC "
        "FETCH NEXT 51 ROWS ONLY) CTK\n"
        "FilialFilter=false\n"
        "DeletedFilter=false\n"
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
        "Como o Protheus não retorna a coluna `R_E_C_N_O_` nos itens, o recno de cada "
        "linha viaja numa coluna numérica emprestada de outra tabela (artifício parecido "
        "com o da `A10` usado no `table-count`), o que permite calcular os cursores sem "
        "uma segunda chamada. Não há retorno do total de registros — "
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

    # Colunas "emprestadas" que carregam o recno de cada linha e o escalar do
    # previous_recno (só calculado quando não é a primeira página).
    (recno_table, recno_col), (prev_table, prev_col) = [
        pair for pair in _SENTINEL_COLUMNS if pair[0] != table
    ][:2]
    prev_expr = (
        _PREV_EXPR_TEMPLATE.format(
            table=physical_table, where_above=where_above, pagesize=pagesize
        )
        if recno
        else "0"
    )

    data, error_response = _protheus_query(
        {
            "tables": f"{table},{recno_table},{prev_table}",
            "fields": f"{fields},{recno_col},{prev_col}",
            # Uma linha a mais que a página para saber se há próxima página.
            "pagesize": str(pagesize + 1),
            "FromQry": _FROM_QRY_TEMPLATE.format(
                table=physical_table,
                where=where_page,
                rows=pagesize + 1,
                alias=table,
                recno_col=recno_col,
                prev_col=prev_col,
                prev_expr=prev_expr,
            ),
            "FilialFilter": "false",
            # O filtro automático de deleção qualificaria D_E_L_E_T_ também nos
            # aliases das tabelas emprestadas (inexistentes no FromQry); a
            # condição já está embutida em where_page.
            "DeletedFilter": "false",
        }
    )
    if error_response is not None:
        return error_response

    recno_key, prev_key = recno_col.lower(), prev_col.lower()
    rows: list[dict[str, Any]] = data.get("items", [])
    rows.sort(key=lambda row: _to_int(row.get(recno_key)) or 0, reverse=True)
    page_rows = rows[:pagesize]

    # Há próxima página se a janela pagesize+1 veio cheia; o cursor é o menor
    # recno da página atual (a próxima página são os registros abaixo dele).
    next_recno: int | None = None
    if len(rows) > pagesize and page_rows:
        next_recno = _to_int(page_rows[-1].get(recno_key))

    if recno == 0:
        # Primeira página: não há anterior.
        previous_recno: int | None = None
    else:
        # O escalar (repetido em todas as linhas) é o pagesize-ésimo recno
        # acima do cursor; NULL quando a anterior é a primeira página.
        previous_recno = (_to_int(page_rows[0].get(prev_key)) if page_rows else None) or 0

    items = [
        {k: v for k, v in row.items() if k not in (recno_key, prev_key)}
        for row in page_rows
    ]

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
