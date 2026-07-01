import csv
import io
import json
import logging
import os
from typing import Any

import azure.functions as func
import requests

from azure_functions_openapi import openapi

bp = func.Blueprint()

_PROTHEUS_BASE_URL = os.environ.get("PROTHEUS_BASE_URL", "")
_PROTHEUS_AUTH = (
    os.environ.get("PROTHEUS_USER", ""),
    os.environ.get("PROTHEUS_PASSWORD", ""),
)

_ERROR_SCHEMA = {
    "type": "object",
    "properties": {"error": {"type": "string", "example": "Mensagem de erro"}},
    "required": ["error"],
}


@openapi(
    summary="Consulta genérica em tabela do Protheus",
    description=(
        "Executa uma consulta parametrizável em qualquer tabela do Protheus via **genericQuery**.\n\n"
        "Exemplo de chamada:\n"
        "```\n"
        "GET /api/generic-query"
        "?table=SE5"
        "&fields=E5_FILIAL,E5_NUM,E5_VALOR"
        "&where=SE5.D_E_L_E_T_=' '"
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
            "description": "Nome da tabela no Protheus (ex: SE5, SA1, SB1)",
        },
        {
            "name": "fields",
            "in": "query",
            "required": True,
            "schema": {"type": "string", "example": "E5_FILIAL,E5_NUM,E5_VALOR"},
            "description": "Colunas desejadas separadas por vírgula",
        }
    ],
    response={
        200: {
            "description": "Dados retornados com sucesso em formato CSV",
            "content": {
                "text/csv": {
                    "schema": {"type": "string"},
                    "example": "E5_FILIAL,E5_NUM,E5_VALOR\r\n01,000001,1500.0\r\n01,000002,320.5\r\n",
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
    operation_id="genericQuery",
)
@bp.route(route="generic-query", methods=["GET"], auth_level=func.AuthLevel.FUNCTION)
def generic_query(req: func.HttpRequest) -> func.HttpResponse:
    table  = req.params.get("table",  "").strip()
    fields = req.params.get("fields", "").strip()

    missing = [p for p, v in [("table", table), ("fields", fields)] if not v]
    if missing:
        return func.HttpResponse(
            json.dumps({"error": f"Parâmetros obrigatórios ausentes: {', '.join(missing)}"}),
            status_code=400,
            mimetype="application/json",
        )

    query_params: dict[str, str] = {
        "tables":   table,
        "fields":   fields,
        "pagesize": 99999999999999,
    }

    try:
        resp = requests.get(
            f"{_PROTHEUS_BASE_URL}/api/framework/v1/genericQuery",
            params=query_params,
            auth=_PROTHEUS_AUTH if all(_PROTHEUS_AUTH) else None,
            timeout=30,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        logging.error("Protheus genericQuery failed: %s", exc)
        return func.HttpResponse(
            json.dumps({"error": "Falha ao conectar à API do Protheus"}),
            status_code=502,
            mimetype="application/json",
        )

    data = resp.json()
    items: list[dict[str, Any]] = data.get("items", [])

    output = io.StringIO()
    if items:
        writer = csv.DictWriter(output, fieldnames=items[0].keys())
        writer.writeheader()
        writer.writerows(items)

    return func.HttpResponse(
        output.getvalue(),
        status_code=200,
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{table}.csv"'},
    )
