"""Handler do endpoint ``GET /databricks/table-count`` — conta registros de uma tabela."""

import logging
from typing import Any

import azure.functions as func

from azure_functions_openapi import openapi

from src.utils.auth import require_roles
from src.utils.databricks import (
    DATABRICKS_CATALOG,
    DATABRICKS_SCHEMA,
    databricks_config_ok,
    fetch_all,
    is_valid_identifier,
    json_error,
)
from src.utils.databricks.filters import parse_filters

from .docs import DOCS
from .models import TableCountResponse

bp = func.Blueprint()


@openapi(**DOCS)
@bp.route(
    route="databricks/table-count",
    methods=["GET"],
    auth_level=func.AuthLevel.ANONYMOUS,
)
@require_roles("Tables.Read")
def databricks_table_count(req: func.HttpRequest) -> func.HttpResponse:
    if not databricks_config_ok():
        return json_error("Conexão com o Databricks não configurada", 503)

    table = req.params.get("table", "").strip()
    if not table:
        return json_error("Parâmetro obrigatório ausente: table", 400)
    if not is_valid_identifier(table):
        return json_error(f"Nome de tabela inválido: '{table}'", 400)

    # catalog e schema são fixos, vindos da configuração de ambiente.
    catalog = DATABRICKS_CATALOG
    schema = DATABRICKS_SCHEMA
    if not is_valid_identifier(catalog) or not is_valid_identifier(schema):
        return json_error("Configuração inválida: DATABRICKS_CATALOG/SCHEMA", 500)

    where_sql = ""
    params: list[Any] = []
    filters_raw = req.params.get("filters", "").strip()
    if filters_raw:
        conditions, filter_params, err = parse_filters(filters_raw)
        if err:
            return json_error(err, 400)
        if conditions:
            where_sql = " WHERE " + " AND ".join(conditions)
            params.extend(filter_params)

    query = f"SELECT COUNT(*) AS total FROM {catalog}.{schema}.{table}{where_sql}"

    try:
        rows = fetch_all(query, params)
    except Exception as exc:  # noqa: BLE001 — falha de conexão/consulta ao Databricks
        logging.error("Databricks table-count failed: %s", exc)
        return json_error("Falha ao consultar o Databricks", 502)

    count = int(rows[0]["total"]) if rows else 0

    return func.HttpResponse(
        TableCountResponse(tabela=table, count=count).model_dump_json(),
        status_code=200,
        mimetype="application/json",
    )
