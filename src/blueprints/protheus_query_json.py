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

# FromQry com o WHERE (deleção e filtros dinâmicos) embutido na subquery,
# sem usar o parâmetro 'where' do Protheus. A paginação é feita por
# OFFSET/FETCH sobre R_E_C_N_O_: busca-se pagesize+1 linhas e a linha extra
# indica se há próxima página (o Protheus não retorna a coluna R_E_C_N_O_).
_FROM_QRY_TEMPLATE = (
    "(SELECT * FROM {table} WHERE {where} "
    "ORDER BY R_E_C_N_O_ ASC OFFSET {offset} ROWS FETCH NEXT {rows} ROWS ONLY) {alias}"
)

_ERROR_SCHEMA = {
    "type": "object",
    "properties": {"error": {"type": "string", "example": "Mensagem de erro"}},
    "required": ["error"],
}


class QueryResponse(BaseModel):
    page: int = Field(description="Página retornada (começa em 1).")
    pagesize: int = Field(description="Registros por página.")
    has_next: bool = Field(description="Indica se há uma próxima página.")
    has_previous: bool = Field(description="Indica se há uma página anterior.")
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


@openapi(
    summary="Consulta em tabela do Protheus (JSON) com filtros dinâmicos",
    description=(
        "Executa uma consulta parametrizável em qualquer tabela do Protheus via **genericQuery** "
        "e retorna os dados em **JSON**.\n\n"
        "A tabela é informada pelo **alias** (ex: `CTK`, `SB1`); o nome físico é montado "
        f"com o sufixo `{_TABLE_SUFFIX}` (ex: `CTK` → `CTK{_TABLE_SUFFIX}`).\n\n"
        "Internamente a genericQuery é chamada com os parâmetros no **header** — o WHERE "
        "(deleção e filtros) fica embutido no `FromQry`, sem usar o parâmetro "
        "`where` do Protheus:\n"
        "```\n"
        "tables=CTK\n"
        "fields=CTK_FILIAL,CTK_CODFOR,CTK_CODCLI,CTK_SEQUEN\n"
        "pagesize=50\n"
        f"FromQry=(SELECT * FROM CTK{_TABLE_SUFFIX} WHERE R_E_C_N_O_ > 0 AND "
        "D_E_L_E_T_ <> '*' ORDER BY R_E_C_N_O_ ASC OFFSET 0 ROWS "
        "FETCH NEXT 51 ROWS ONLY) CTK\n"
        "FilialFilter=false\n"
        "```\n\n"
        "### Paginação (page + pagesize)\n"
        "A subquery busca sempre `pagesize + 1` registros (ex: 51 para `pagesize=50`), "
        "mas somente `pagesize` são devolvidos ao usuário — o registro extra indica se "
        "existe próxima página (`has_next`). Para avançar, chame novamente com "
        "`page + 1`; para voltar, com `page - 1` (`has_previous` indica se há página "
        "anterior). Não há retorno do total de registros.\n\n"
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
                        "page": 1,
                        "pagesize": 50,
                        "has_next": True,
                        "has_previous": False,
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

    page = _parse_int(req.params.get("page", ""), 1)
    pagesize = _parse_int(req.params.get("pagesize", ""), _DEFAULT_PAGESIZE)
    if page is None or pagesize is None or page < 1 or pagesize < 1:
        return func.HttpResponse(
            json.dumps({"error": "'page' e 'pagesize' devem ser inteiros >= 1"}),
            status_code=400,
            mimetype="application/json",
        )

    pagesize = min(pagesize, _MAX_PAGESIZE)

    where_parts = ["R_E_C_N_O_ > 0", "D_E_L_E_T_ <> '*'"]

    if filters_raw:
        conditions, err = parse_filters(filters_raw)
        if err:
            return func.HttpResponse(
                json.dumps({"error": err}),
                status_code=400,
                mimetype="application/json",
            )
        where_parts.extend(conditions)

    physical_table = f"{table}{_TABLE_SUFFIX}"

    # Busca pagesize+1 linhas: a extra indica se há próxima página,
    # mas somente pagesize registros são devolvidos ao usuário.
    from_qry = _FROM_QRY_TEMPLATE.format(
        table=physical_table,
        where=" AND ".join(where_parts),
        offset=(page - 1) * pagesize,
        rows=pagesize + 1,
        alias=table,
    )

    headers = {
        "tables": table,
        "fields": fields,
        "pagesize": str(pagesize + 1),
        "FromQry": from_qry,
        "FilialFilter": "false",
    }

    try:
        resp = requests.get(
            f"{_PROTHEUS_BASE_URL}/api/framework/v1/genericQuery",
            auth=_PROTHEUS_AUTH if all(_PROTHEUS_AUTH) else None,
            timeout=60*10,
            headers=headers,
        )
    except requests.RequestException as exc:
        logging.error("Protheus queryJson failed: %s", exc)
        return func.HttpResponse(
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
        return func.HttpResponse(
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
        data = resp.json()
    except ValueError:
        # Protheus respondeu algo que não é JSON (corpo vazio, HTML de erro, etc.).
        # Causa comum: muitos campos em 'fields' ou campo inválido.
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

    has_next = len(items) > pagesize
    items = items[:pagesize]

    return func.HttpResponse(
        QueryResponse(
            page=page,
            pagesize=pagesize,
            has_next=has_next,
            has_previous=page > 1,
            items=items,
        ).model_dump_json(),
        status_code=200,
        mimetype="application/json",
    )
