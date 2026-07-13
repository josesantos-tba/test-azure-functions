import json
import logging
import os
import re

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

# Alias de tabela válido (evita injeção via nome de tabela no FromQry).
_TABLE_RE = re.compile(r"^[A-Za-z0-9_]+$")

# Sufixo padrão que converte o alias (ex: CTK) no nome físico (ex: CTK010).
_TABLE_SUFFIX = "010"

# FromQry que "engana" o genericQuery: usa a estrutura da A10 e devolve o
# COUNT(*) da tabela desejada na coluna A10_PRAZO. O WHERE usa o mesmo
# critério do query-json (deleção + filtros dinâmicos) para a contagem
# bater com o que a listagem retorna.
_FROM_QRY_TEMPLATE = (
    "(SELECT '' AS A10_FILIAL,"
    "'' AS A10_CJETAP,"
    "'' AS A10_ETAPA,"
    "'' AS A10_ETDESC,"
    "'' AS A10_WKFLOW,"
    "(SELECT COUNT(*) FROM {table} WHERE {where}) AS A10_PRAZO,"
    " 1 AS R_E_C_N_O_,"
    "0 AS R_E_C_D_E_L_,"
    "' ' AS D_E_L_E_T_ FROM DUAL) A10"
)

_ERROR_SCHEMA = {
    "type": "object",
    "properties": {"error": {"type": "string", "example": "Mensagem de erro"}},
    "required": ["error"],
}


class TableCountResponse(BaseModel):
    table: str = Field(description="Alias da tabela contada (ex: CTK).")
    count: int = Field(
        description=(
            "Quantidade de registros não deletados na tabela que atendem aos "
            "filtros informados (todos, se nenhum filtro)."
        )
    )


@openapi(
    summary="Quantidade de registros de uma tabela do Protheus",
    description=(
        "Retorna a quantidade de registros não deletados de uma tabela do Protheus, "
        "informada pelo **alias** (ex: `CTK`, `SB1`). O nome físico é montado com o "
        f"sufixo `{_TABLE_SUFFIX}` (ex: `CTK` → `CTK{_TABLE_SUFFIX}`).\n\n"
        "Aceita os mesmos **filtros dinâmicos** (`filters`) do endpoint `query-json`, "
        "permitindo contar apenas os registros que atendem às condições — útil para "
        "calcular o total de páginas de uma consulta filtrada. O WHERE usado é o "
        "mesmo do `query-json` (`R_E_C_N_O_ > 0 AND D_E_L_E_T_ <> '*'` + filtros), "
        "então a contagem corresponde ao que a listagem retorna.\n\n"
        "Internamente executa a **genericQuery** passando os parâmetros no **header**:\n"
        "```\n"
        "tables=A10\n"
        "fields=A10_PRAZO\n"
        "pagesize=1\n"
        "FromQry=(SELECT '' AS A10_FILIAL,'' AS A10_CJETAP,'' AS A10_ETAPA,"
        "'' AS A10_ETDESC,'' AS A10_WKFLOW,(SELECT COUNT(*) FROM <tabela> "
        "WHERE R_E_C_N_O_ > 0 AND D_E_L_E_T_ <> '*' AND <filtros>) AS A10_PRAZO, "
        "1 AS R_E_C_N_O_,0 AS R_E_C_D_E_L_,"
        "' ' AS D_E_L_E_T_ FROM DUAL) A10\n"
        "FilialFilter=false\n"
        "```\n"
        "O `COUNT(*)` da tabela informada é devolvido pelo Protheus na coluna "
        "`a10_prazo`, que é usada como resposta."
    ),
    tags=["Protheus"],
    method="get",
    parameters=[
        {
            "name": "table",
            "in": "query",
            "required": True,
            "schema": {"type": "string", "example": "CTK"},
            "description": "Alias da tabela no Protheus (ex: CTK, SB1, SE5)",
        },
        filters_openapi_param(
            '[{"column":"E5_VALOR","operator":">=","value":1000,"type":"number"},'
            '{"column":"E5_NUMERO","operator":"comeca com","value":"0001"}]'
        ),
    ],
    response={
        200: {
            "description": "Quantidade retornada com sucesso",
            "content": {
                "application/json": {
                    "schema": inline_refs(TableCountResponse.model_json_schema()),
                    "example": {"table": "CTK", "count": 21410759},
                }
            },
        },
        400: {
            "description": "Parâmetro 'table' ausente/inválido ou 'filters' inválido",
            "content": {
                "application/json": {
                    "schema": _ERROR_SCHEMA,
                    "example": {"error": "Parâmetro obrigatório ausente: table"},
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
    operation_id="tableCount",
)
@bp.route(route="table-count", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
@require_roles("Tables.Read")
def table_count(req: func.HttpRequest) -> func.HttpResponse:

    table = req.params.get("table", "").strip().upper()
    filters_raw = req.params.get("filters", "").strip()

    if not table:
        return func.HttpResponse(
            json.dumps({"error": "Parâmetro obrigatório ausente: table"}),
            status_code=400,
            mimetype="application/json",
        )

    if not _TABLE_RE.match(table):
        return func.HttpResponse(
            json.dumps({"error": f"Alias de tabela inválido: '{table}'"}),
            status_code=400,
            mimetype="application/json",
        )

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

    headers = {
        "tables": "A10",
        "fields": "A10_PRAZO",
        "pagesize": "1",
        "FromQry": _FROM_QRY_TEMPLATE.format(
            table=physical_table,
            where=" AND ".join(where_parts),
        ),
        "FilialFilter": "false",
    }

    try:
        resp = requests.get(
            f"{_PROTHEUS_BASE_URL}/api/framework/v1/genericQuery",
            auth=_PROTHEUS_AUTH if all(_PROTHEUS_AUTH) else None,
            timeout=60 * 10,
            headers=headers,
        )
    except requests.RequestException as exc:
        logging.error("Protheus tableCount failed: %s", exc)
        return func.HttpResponse(
            json.dumps({"error": "Falha ao conectar à API do Protheus"}),
            status_code=502,
            mimetype="application/json",
        )

    if resp.status_code >= 400:
        # Causa comum de 500 no Protheus: coluna inexistente em 'filters'.
        logging.error(
            "Protheus tableCount erro HTTP %s: %.500s", resp.status_code, resp.text
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
        logging.error(
            "Protheus tableCount retornou resposta não-JSON (status=%s): %.500s",
            resp.status_code,
            resp.text,
        )
        return func.HttpResponse(
            json.dumps({"error": "Resposta inválida do Protheus (não-JSON)."}),
            status_code=502,
            mimetype="application/json",
        )

    items = data.get("items", [])
    if not items or "a10_prazo" not in items[0]:
        logging.error("Protheus tableCount sem 'a10_prazo' na resposta: %.500s", resp.text)
        return func.HttpResponse(
            json.dumps({"error": "Resposta do Protheus sem a coluna 'a10_prazo'."}),
            status_code=502,
            mimetype="application/json",
        )

    return func.HttpResponse(
        TableCountResponse(table=table, count=int(items[0]["a10_prazo"])).model_dump_json(),
        status_code=200,
        mimetype="application/json",
    )
