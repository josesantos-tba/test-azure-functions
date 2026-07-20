"""Handler do endpoint ``GET /databricks/query-json`` — consulta paginada em JSON."""

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
from .models import DEFAULT_PAGESIZE, MAX_PAGESIZE, QueryResponse

bp = func.Blueprint()


def _parse_int(value: str, default: int) -> int | None:
    """Retorna o inteiro contido em ``value`` ou ``default`` se vazio; ``None`` se inválido."""
    value = value.strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return None


def _validate_fields(raw: str) -> tuple[str | None, str | None]:
    """Valida a lista de colunas de ``fields`` e devolve ``(fields_sql, None)``.

    Sem ``fields`` retorna ``("*", None)``. Cada coluna é validada como
    identificador para poder ser interpolada com segurança na projeção.
    """
    raw = raw.strip()
    if not raw:
        return "*", None
    columns = [c.strip() for c in raw.split(",") if c.strip()]
    for column in columns:
        if not is_valid_identifier(column):
            return None, f"Nome de coluna inválido em 'fields': '{column}'"
    return ", ".join(columns), None


@openapi(**DOCS)
@bp.route(
    route="databricks/query-json",
    methods=["GET"],
    auth_level=func.AuthLevel.ANONYMOUS,
)
@require_roles("Tables.Read")
def databricks_query_json(req: func.HttpRequest) -> func.HttpResponse:
    if not databricks_config_ok():
        return json_error("Conexão com o Databricks não configurada", 503)

    table = req.params.get("table", "").strip()
    if not table:
        return json_error("Parâmetro obrigatório ausente: table", 400)
    if not is_valid_identifier(table):
        return json_error(f"Nome de tabela inválido: '{table}'", 400)

    fields_sql, err = _validate_fields(req.params.get("fields", ""))
    if err:
        return json_error(err, 400)

    # catalog e schema são fixos, vindos da configuração de ambiente.
    catalog = DATABRICKS_CATALOG
    schema = DATABRICKS_SCHEMA
    if not is_valid_identifier(catalog) or not is_valid_identifier(schema):
        return json_error("Configuração inválida: DATABRICKS_CATALOG/SCHEMA", 500)

    page = _parse_int(req.params.get("page", ""), 1)
    pagesize = _parse_int(req.params.get("pagesize", ""), DEFAULT_PAGESIZE)
    if page is None or page < 1:
        return json_error("'page' deve ser um inteiro >= 1", 400)
    if pagesize is None or pagesize < 1:
        return json_error("'pagesize' deve ser um inteiro >= 1", 400)
    pagesize = min(pagesize, MAX_PAGESIZE)

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

    # Busca uma linha a mais que a página para saber se há próxima.
    offset = (page - 1) * pagesize
    query = (
        f"SELECT {fields_sql} FROM {catalog}.{schema}.{table}"
        f"{where_sql} "
        f"LIMIT {pagesize + 1} OFFSET {offset}"
    )

    try:
        rows = fetch_all(query, params)
    except Exception as exc:  # noqa: BLE001 — falha de conexão/consulta ao Databricks
        logging.error("Databricks query-json failed: %s", exc)
        return json_error("Falha ao consultar o Databricks", 502)

    has_next = len(rows) > pagesize
    items = rows[:pagesize]

    return func.HttpResponse(
        QueryResponse(
            tabela=table,
            page=page,
            pagesize=pagesize,
            has_next=has_next,
            next_page=page + 1 if has_next else None,
            items=items,
        ).model_dump_json(),
        status_code=200,
        mimetype="application/json",
    )
