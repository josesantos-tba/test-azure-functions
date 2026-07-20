"""Handler do endpoint ``GET /databricks/export-csv`` — exporta uma tabela em CSV."""

import csv
import io
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
from .models import MAX_ROWS

bp = func.Blueprint()


@openapi(**DOCS)
@bp.route(
    route="databricks/export-csv",
    methods=["GET"],
    auth_level=func.AuthLevel.ANONYMOUS,
)
@require_roles("Tables.Read")
def databricks_export_csv(req: func.HttpRequest) -> func.HttpResponse:
    if not databricks_config_ok():
        return json_error("Conexão com o Databricks não configurada", 503)

    table = req.params.get("table", "").strip()
    if not table:
        return json_error("Parâmetro obrigatório ausente: table", 400)
    if not is_valid_identifier(table):
        return json_error(f"Nome de tabela inválido: '{table}'", 400)

    # fields: lista de colunas (padrão *). Cada uma é validada como identificador.
    fields_raw = req.params.get("fields", "").strip()
    columns = [c.strip() for c in fields_raw.split(",") if c.strip()]
    for column in columns:
        if not is_valid_identifier(column):
            return json_error(f"Nome de coluna inválido em 'fields': '{column}'", 400)
    fields_sql = ", ".join(columns) if columns else "*"

    # catalog e schema são fixos, vindos da configuração de ambiente.
    catalog = DATABRICKS_CATALOG
    schema = DATABRICKS_SCHEMA
    if not is_valid_identifier(catalog) or not is_valid_identifier(schema):
        return json_error("Configuração inválida: DATABRICKS_CATALOG/SCHEMA", 500)

    limit_raw = req.params.get("limit", "").strip()
    if limit_raw:
        try:
            limit = int(limit_raw)
        except ValueError:
            limit = 0
        if limit < 1:
            return json_error("'limit' deve ser um inteiro >= 1", 400)
        limit = min(limit, MAX_ROWS)
    else:
        limit = MAX_ROWS

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

    query = (
        f"SELECT {fields_sql} FROM {catalog}.{schema}.{table}"
        f"{where_sql} "
        f"LIMIT {limit}"
    )

    try:
        rows = fetch_all(query, params)
    except Exception as exc:  # noqa: BLE001 — falha de conexão/consulta ao Databricks
        logging.error("Databricks export-csv failed: %s", exc)
        return json_error("Falha ao consultar o Databricks", 502)

    # Cabeçalho: usa as colunas de 'fields' (na ordem informada) quando dado;
    # caso contrário, deriva das chaves da primeira linha (SELECT *).
    if columns:
        header = columns
    elif rows:
        header = list(rows[0].keys())
    else:
        header = []

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(header)
    for row in rows:
        writer.writerow(["" if row.get(k) is None else row.get(k) for k in header])

    # BOM (utf-8-sig) para o Excel reconhecer a codificação UTF-8.
    return func.HttpResponse(
        buffer.getvalue().encode("utf-8-sig"),
        status_code=200,
        mimetype="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{table}.csv"',
            "X-Row-Count": str(len(rows)),
        },
    )
