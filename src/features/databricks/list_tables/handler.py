"""Handler do endpoint ``GET /databricks/tables`` — lista tabelas de um schema."""

import logging

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

from .docs import DOCS
from .models import DatabricksTable, DatabricksTablesResponse

bp = func.Blueprint()


@openapi(**DOCS)
@bp.route(route="databricks/tables", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
@require_roles("Tables.Read")
def databricks_list_tables(req: func.HttpRequest) -> func.HttpResponse:
    if not databricks_config_ok():
        return json_error("Conexão com o Databricks não configurada", 503)

    # catalog e schema são fixos, vindos da configuração de ambiente.
    catalog = DATABRICKS_CATALOG
    schema = DATABRICKS_SCHEMA

    # catalog é interpolado no FROM (não pode ser bind parameter) → validar.
    if not is_valid_identifier(catalog):
        return json_error("Configuração inválida: DATABRICKS_CATALOG", 500)

    # Retorna somente as tabelas cujo nome começa com 'dbo_' (o que já exclui as
    # internas de materialização '__') e ignora as streaming tables. Usa substr
    # (não LIKE, que trataria '_' como curinga; e não startswith, cujo predicado
    # é mal empurrado para o metastore no information_schema).
    query = (
        f"SELECT table_catalog, table_schema, table_name "
        f"FROM {catalog}.information_schema.tables "
        f"WHERE table_schema = ? "
        f"AND substr(table_name, 1, 4) = 'dbo_' "
        f"AND table_type <> 'STREAMING_TABLE' "
        f"ORDER BY table_name"
    )

    try:
        rows = fetch_all(query, [schema])
    except Exception as exc:  # noqa: BLE001 — falha de conexão/consulta ao Databricks
        logging.error("Databricks query failed: %s", exc)
        return json_error("Falha ao consultar o Databricks", 502)

    tables = [
        DatabricksTable(
            catalogo=row.get("table_catalog", ""),
            schema=row.get("table_schema", ""),
            nome=row.get("table_name", ""),
            # Nome amigável: sem o prefixo 'dbo_' (o filtro garante que todas
            # começam com ele).
            display_name=row.get("table_name", "").removeprefix("dbo_"),
        )
        for row in rows
    ]

    return func.HttpResponse(
        DatabricksTablesResponse(
            catalogo=catalog, schema=schema, tabelas=tables
        ).model_dump_json(by_alias=True),
        status_code=200,
        mimetype="application/json",
    )
