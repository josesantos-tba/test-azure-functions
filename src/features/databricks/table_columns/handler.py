"""Handler do endpoint ``GET /databricks/table-columns`` — colunas de uma tabela."""

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
from .models import DatabricksColumn, DatabricksColumnsResponse

bp = func.Blueprint()


@openapi(**DOCS)
@bp.route(
    route="databricks/table-columns",
    methods=["GET"],
    auth_level=func.AuthLevel.ANONYMOUS,
)
@require_roles("Tables.Read")
def databricks_table_columns(req: func.HttpRequest) -> func.HttpResponse:
    if not databricks_config_ok():
        return json_error("Conexão com o Databricks não configurada", 503)

    table = req.params.get("table", "").strip()
    if not table:
        return json_error("Parâmetro obrigatório ausente: table", 400)

    # catalog e schema são fixos, vindos da configuração de ambiente.
    catalog = DATABRICKS_CATALOG
    schema = DATABRICKS_SCHEMA

    # catalog é interpolado no FROM (não pode ser bind parameter) → validar.
    if not is_valid_identifier(catalog):
        return json_error("Configuração inválida: DATABRICKS_CATALOG", 500)

    query = (
        f"SELECT column_name, data_type, ordinal_position, is_nullable, comment "
        f"FROM {catalog}.information_schema.columns "
        f"WHERE table_schema = ? AND table_name = ? "
        f"ORDER BY ordinal_position"
    )

    try:
        rows = fetch_all(query, [schema, table])
    except Exception as exc:  # noqa: BLE001 — falha de conexão/consulta ao Databricks
        logging.error("Databricks query failed: %s", exc)
        return json_error("Falha ao consultar o Databricks", 502)

    columns = [
        DatabricksColumn(
            nome=row.get("column_name", ""),
            tipo=row.get("data_type", ""),
            posicao=int(row.get("ordinal_position", 0) or 0),
            nullable=str(row.get("is_nullable", "")).upper() == "YES",
            comentario=row.get("comment"),
        )
        for row in rows
    ]

    return func.HttpResponse(
        DatabricksColumnsResponse(
            catalogo=catalog, schema=schema, tabela=table, colunas=columns
        ).model_dump_json(by_alias=True),
        status_code=200,
        mimetype="application/json",
    )
