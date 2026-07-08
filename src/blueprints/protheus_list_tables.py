import json
import logging
import os

import azure.functions as func
import requests
from pydantic import BaseModel

from azure_functions_openapi import openapi

from src.utils.auth import require_roles
from src.utils.openapi import inline_refs

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


class ProtheusTable(BaseModel):
    chave: str
    nome: str
    modo: str
    modulo: int
    pyme: str


class TablesResponse(BaseModel):
    tabelas: list[ProtheusTable]


@openapi(
    summary="Lista de tabelas do Protheus",
    description=(
        "Consulta a tabela de dicionário **SX2** no Protheus e retorna todas as "
        "tabelas cadastradas (chave, nome, modo, módulo e indicador PYME).\n\n"
        "Internamente executa:\n"
        "```\n"
        "tables=SX2&fields=X2_CHAVE,X2_NOME,X2_MODO,X2_MODULO,X2_PYME\n"
        "&where=SX2.D_E_L_E_T_=' '\n"
        "```"
    ),
    tags=["Protheus"],
    method="get",
    parameters=[],
    response={
        200: {
            "description": "Lista de tabelas retornada com sucesso",
            "content": {
                "application/json": {
                    "schema": inline_refs(TablesResponse.model_json_schema()),
                    "example": {
                        "tabelas": [
                            {
                                "chave": "A00",
                                "nome": "Território x Nível do Agrup.",
                                "modo": "C",
                                "modulo": 73,
                                "pyme": "S",
                            },
                        ],
                    },
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
    operation_id="listTables",
)
@bp.route(route="tables", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
@require_roles("Tables.Read")
def list_tables(req: func.HttpRequest) -> func.HttpResponse:
    url = f"{_PROTHEUS_BASE_URL}/api/framework/v1/genericQuery"
    params = {
        "tables": "SX2",
        "fields": "X2_CHAVE,X2_NOME,X2_MODO,X2_MODULO,X2_PYME",
        "where": "SX2.D_E_L_E_T_=' '",
        "page": "1",
        "pagesize": "9999999",
    }

    headers = {
        "FilialFilter": "false"
    }

    try:
        resp = requests.get(
            url,
            params=params,
            auth=_PROTHEUS_AUTH if all(_PROTHEUS_AUTH) else None,
            timeout=30,
            headers=headers
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        logging.error("Protheus request failed: %s", exc)
        return func.HttpResponse(
            json.dumps({"error": "Falha ao conectar à API do Protheus"}),
            status_code=502,
            mimetype="application/json",
        )

    items = resp.json().get("items", [])
    tables = [
        ProtheusTable(
            chave=item.get("x2_chave", "").strip(),
            nome=item.get("x2_nome", "").strip(),
            modo=item.get("x2_modo", "").strip(),
            modulo=int(item.get("x2_modulo", 0)),
            pyme=item.get("x2_pyme", "").strip(),
        )
        for item in items
    ]

    return func.HttpResponse(
        TablesResponse(tabelas=tables).model_dump_json(),
        status_code=200,
        mimetype="application/json",
    )
