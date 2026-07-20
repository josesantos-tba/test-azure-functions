"""Handler do endpoint ``GET /export-csv`` — exporta uma tabela em CSV."""

import csv
import io
import logging
import re

import azure.functions as func
import requests

from azure_functions_openapi import openapi

from src.utils.auth import require_roles
from src.utils.protheus import GENERIC_QUERY_URL, json_error, protheus_auth
from src.utils.protheus_filters import parse_filters

from .docs import DOCS
from .models import MAX_ROWS

bp = func.Blueprint()

# Alias de tabela válido.
_TABLE_RE = re.compile(r"^[A-Za-z0-9_]+$")


@openapi(**DOCS)
@bp.route(route="export-csv", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
@require_roles("Tables.Read")
def export_csv(req: func.HttpRequest) -> func.HttpResponse:

    table = req.params.get("table", "").strip().upper()
    fields = req.params.get("fields", "").strip()
    filters_raw = req.params.get("filters", "").strip()
    limit_raw = req.params.get("limit", "").strip()

    missing = [p for p, v in [("table", table), ("fields", fields)] if not v]
    if missing:
        return json_error(
            f"Parâmetros obrigatórios ausentes: {', '.join(missing)}", 400
        )

    if not _TABLE_RE.match(table):
        return json_error(f"Alias de tabela inválido: '{table}'", 400)

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

    where_parts: list[str] = []

    if filters_raw:
        conditions, err = parse_filters(filters_raw)
        if err:
            return json_error(err, 400)
        where_parts.extend(conditions)

    # Parâmetros nativos da genericQuery: o corte de linhas fica por conta do
    # 'pagesize' e os filtros dinâmicos vão no 'where'. O filtro de deleção é
    # o automático da API (DeletedFilter, ligado por padrão).
    headers = {
        "tables": table,
        "fields": fields,
        "pagesize": str(limit),
        "FilialFilter": "false",
    }
    if where_parts:
        headers["where"] = " AND ".join(where_parts)

    try:
        resp = requests.get(
            GENERIC_QUERY_URL,
            auth=protheus_auth(),
            timeout=60 * 10,
            headers=headers,
        )
    except requests.RequestException as exc:
        logging.error("Protheus exportCsv failed: %s", exc)
        return json_error("Falha ao conectar à API do Protheus", 502)

    if resp.status_code >= 400:
        # Causa comum de 500 no Protheus: coluna inexistente em 'filters'
        # (em 'fields' o Protheus apenas ignora a coluna).
        logging.error(
            "Protheus exportCsv erro HTTP %s: %.500s", resp.status_code, resp.text
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
            "Protheus exportCsv retornou resposta não-JSON (status=%s): %.500s",
            resp.status_code,
            resp.text,
        )
        return json_error(
            "Resposta inválida do Protheus (não-JSON). "
            "Verifique os campos e o tamanho da consulta.",
            502,
        )

    items = data.get("items", [])[:limit]

    # Cabeçalho na ordem de 'fields'; os valores vêm do JSON do Protheus,
    # cujas chaves são os nomes das colunas em minúsculas. Colunas protegidas
    # (não retornadas) saem vazias.
    columns = [c.strip() for c in fields.split(",") if c.strip()]
    keys = [c.lower() for c in columns]

    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(columns)
    for item in items:
        writer.writerow(
            ["" if item.get(k) is None else item.get(k) for k in keys]
        )

    # BOM (utf-8-sig) para o Excel reconhecer a codificação UTF-8.
    return func.HttpResponse(
        buffer.getvalue().encode("utf-8-sig"),
        status_code=200,
        mimetype="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="{table}.csv"',
            "X-Row-Count": str(len(items)),
        },
    )
