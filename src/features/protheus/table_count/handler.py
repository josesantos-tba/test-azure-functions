"""Handler do endpoint ``GET /table-count`` — conta registros de uma tabela."""

import logging
import re

import azure.functions as func
import requests

from azure_functions_openapi import openapi

from src.utils.auth import require_roles
from src.utils.protheus import GENERIC_QUERY_URL, json_error, protheus_auth
from src.utils.protheus_filters import parse_filters
from src.utils.table_config import mandatory_filter, table_enabled

from .docs import DOCS
from .models import TABLE_SUFFIX, TableCountResponse

bp = func.Blueprint()

# Alias de tabela válido (evita injeção via nome de tabela no FromQry).
_TABLE_RE = re.compile(r"^[A-Za-z0-9_]+$")

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


@openapi(**DOCS)
@bp.route(route="table-count", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
@require_roles("Tables.Read")
def table_count(req: func.HttpRequest) -> func.HttpResponse:

    table = req.params.get("table", "").strip().upper()
    filters_raw = req.params.get("filters", "").strip()

    if not table:
        return json_error("Parâmetro obrigatório ausente: table", 400)

    if not _TABLE_RE.match(table):
        return json_error(f"Alias de tabela inválido: '{table}'", 400)

    # Governança por tabela (src/config/table_config.json).
    if not table_enabled(table):
        return json_error(f"Tabela '{table}' não está habilitada", 403)

    where_parts = ["R_E_C_N_O_ > 0", "D_E_L_E_T_ <> '*'"]

    # Filtros obrigatórios da tabela: sempre aplicados, antes dos filtros do cliente.
    where_parts.extend(mandatory_filter(table))

    if filters_raw:
        conditions, err = parse_filters(filters_raw)
        if err:
            return json_error(err, 400)
        where_parts.extend(conditions)

    physical_table = f"{table}{TABLE_SUFFIX}"

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
            GENERIC_QUERY_URL,
            auth=protheus_auth(),
            timeout=60 * 10,
            headers=headers,
        )
    except requests.RequestException as exc:
        logging.error("Protheus tableCount failed: %s", exc)
        return json_error("Falha ao conectar à API do Protheus", 502)

    if resp.status_code >= 400:
        # Causa comum de 500 no Protheus: coluna inexistente em 'filters'.
        logging.error(
            "Protheus tableCount erro HTTP %s: %.500s", resp.status_code, resp.text
        )
        return json_error(
            f"Protheus retornou erro HTTP {resp.status_code}. "
            "Verifique se as colunas usadas em 'filters' existem na tabela.",
            502,
        )

    try:
        data = resp.json()
    except ValueError:
        logging.error(
            "Protheus tableCount retornou resposta não-JSON (status=%s): %.500s",
            resp.status_code,
            resp.text,
        )
        return json_error("Resposta inválida do Protheus (não-JSON).", 502)

    items = data.get("items", [])
    if not items or "a10_prazo" not in items[0]:
        logging.error("Protheus tableCount sem 'a10_prazo' na resposta: %.500s", resp.text)
        return json_error("Resposta do Protheus sem a coluna 'a10_prazo'.", 502)

    return func.HttpResponse(
        TableCountResponse(table=table, count=int(items[0]["a10_prazo"])).model_dump_json(),
        status_code=200,
        mimetype="application/json",
    )
