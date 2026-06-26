import json
import logging
import os
from typing import Any

import azure.functions as func
import requests
from pydantic import BaseModel

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

_RESPONSE_200_SCHEMA = {
    "type": "object",
    "properties": {
        "total":    {"type": "integer", "description": "Total de registros encontrados"},
        "has_next": {"type": "boolean", "description": "Indica se há mais páginas"},
        "page":     {"type": "integer", "description": "Página atual"},
        "pagesize": {"type": "integer", "description": "Tamanho da página"},
        "items": {
            "type": "array",
            "description": "Registros retornados. As chaves de cada objeto correspondem às colunas solicitadas.",
            "items": {
                "type": "object",
                "additionalProperties": True,
            },
        },
    },
    "required": ["total", "has_next", "page", "pagesize", "items"],
}


class GenericQueryResponse(BaseModel):
    total: int
    has_next: bool
    page: int
    pagesize: int
    items: list[dict[str, Any]]


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
        "&page=1"
        "&pagesize=50\n"
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
        },
        {
            "name": "where",
            "in": "query",
            "required": False,
            "schema": {"type": "string", "example": "SE5.D_E_L_E_T_=' '"},
            "description": "Condição de filtro SQL (opcional)",
        },
        {
            "name": "page",
            "in": "query",
            "required": False,
            "schema": {"type": "integer", "default": 1, "minimum": 1},
            "description": "Número da página (padrão: 1)",
        },
        {
            "name": "pagesize",
            "in": "query",
            "required": False,
            "schema": {"type": "integer", "default": 100, "minimum": 1, "maximum": 1000},
            "description": "Quantidade de registros por página (padrão: 100, máximo: 1000)",
        },
    ],
    response={
        200: {
            "description": "Dados retornados com sucesso",
            "content": {
                "application/json": {
                    "schema": _RESPONSE_200_SCHEMA,
                    "example": {
                        "total": 2,
                        "has_next": False,
                        "page": 1,
                        "pagesize": 100,
                        "items": [
                            {"E5_FILIAL": "01", "E5_NUM": "000001", "E5_VALOR": 1500.00},
                            {"E5_FILIAL": "01", "E5_NUM": "000002", "E5_VALOR": 320.50},
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

    try:
        page     = int(req.params.get("page",     "1"))
        pagesize = int(req.params.get("pagesize", "100"))
    except ValueError:
        return func.HttpResponse(
            json.dumps({"error": "Os parâmetros page e pagesize devem ser inteiros"}),
            status_code=400,
            mimetype="application/json",
        )

    if page < 1 or pagesize < 1 or pagesize > 1000:
        return func.HttpResponse(
            json.dumps({"error": "page deve ser >= 1 e pagesize deve estar entre 1 e 1000"}),
            status_code=400,
            mimetype="application/json",
        )

    query_params: dict[str, str] = {
        "tables":   table,
        "fields":   fields,
        "page":     str(page),
        "pagesize": str(pagesize),
    }
    where = req.params.get("where", "").strip()
    if where:
        query_params["where"] = where

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
    result = GenericQueryResponse(
        total=data.get("total", 0),
        has_next=data.get("hasNext", False),
        page=page,
        pagesize=pagesize,
        items=data.get("items", []),
    )

    return func.HttpResponse(
        result.model_dump_json(),
        status_code=200,
        mimetype="application/json",
    )
