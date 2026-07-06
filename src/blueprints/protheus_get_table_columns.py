import json
import logging
import os

import azure.functions as func
import requests
from pydantic import BaseModel

from azure_functions_openapi import openapi

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

# Campos que fazem o genericQuery responder "no content" (corpo vazio) quando
# pedidos em 'fields' e por isso são omitidos da lista de colunas:
#   - sufixos de controle/log de acesso do Protheus (_USERLGI, _USERLGA);
#   - campos do tipo Memo ('M'), que a API não consegue serializar.
_EXCLUDED_COLUMN_SUFFIXES = ("_USERLGI", "_USERLGA","_USERGI","USERGA")
_EXCLUDED_COLUMN_TYPES = ("M",)


def _is_excluded_column(campo: str, tipo: str) -> bool:
    """Indica se o campo é interno/incompatível e quebra o genericQuery."""
    return campo.upper().endswith(_EXCLUDED_COLUMN_SUFFIXES) or tipo.upper() in _EXCLUDED_COLUMN_TYPES


class TableColumn(BaseModel):
    campo: str
    titulo: str
    tipo: str
    tamanho: int


class TableColumnsResponse(BaseModel):
    tabela: str
    colunas: list[TableColumn]


@openapi(
    summary="Colunas de uma tabela Protheus",
    description=(
        "Consulta a tabela de dicionário **SX3** no Protheus e retorna as colunas "
        "(campo, título, tipo e tamanho) da tabela informada.\n\n"
        "Internamente executa:\n"
        "```\n"
        "tables=SX3&fields=X3_CAMPO,X3_TITULO,X3_TIPO,X3_TAMANHO\n"
        "&where=SX3.D_E_L_E_T_=' ' AND SX3.X3_ARQUIVO='{table}'\n"
        "```\n\n"
        "Campos que fazem o `genericQuery` responder *no content* são omitidos: "
        "campos de controle/log (sufixos `_USERLGI` e `_USERLGA`) e campos do tipo Memo (`M`)."
    ),
    tags=["Protheus"],
    method="get",
    parameters=[
        {
            "name": "table",
            "in": "query",
            "required": True,
            "schema": {"type": "string", "example": "SE5"},
            "description": "Nome da tabela no dicionário do Protheus (ex: SE5, SA1)",
        }
    ],
    response={
        200: {
            "description": "Lista de colunas retornada com sucesso",
            "content": {
                "application/json": {
                    "schema": inline_refs(TableColumnsResponse.model_json_schema()),
                    "example": {
                        "tabela": "SE5",
                        "colunas": [
                            {"campo": "E5_FILIAL", "titulo": "Filial", "tipo": "C", "tamanho": 2},
                            {"campo": "E5_NUM", "titulo": "Nro Movimento", "tipo": "C", "tamanho": 9},
                        ],
                    },
                }
            },
        },
        400: {
            "description": "Parâmetro obrigatório `table` não informado ou vazio",
            "content": {
                "application/json": {
                    "schema": _ERROR_SCHEMA,
                    "example": {"error": "Parâmetro obrigatório ausente: table"},
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
    operation_id="getTableColumns",
)
@bp.route(route="table-columns", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def get_table_columns(req: func.HttpRequest) -> func.HttpResponse:
    table = req.params.get("table", "").strip().upper()
    if not table:
        return func.HttpResponse(
            json.dumps({"error": "Parâmetro obrigatório ausente: table"}),
            status_code=400,
            mimetype="application/json",
        )

    url = f"{_PROTHEUS_BASE_URL}/api/framework/v1/genericQuery"
    params = {
        "tables": "SX3",
        "fields": "X3_CAMPO,X3_TITULO,X3_TIPO,X3_TAMANHO",
        "where": f"SX3.D_E_L_E_T_=' ' AND SX3.X3_ARQUIVO='{table}'",
        "page": "1",
        "pagesize": "1000",
    }

    try:
        resp = requests.get(
            url,
            params=params,
            auth=_PROTHEUS_AUTH if all(_PROTHEUS_AUTH) else None,
            timeout=30,
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        logging.error("Protheus request failed: %s", exc)
        return func.HttpResponse(
            json.dumps({"error": "Falha ao conectar à API do Protheus"}),
            status_code=502,
            mimetype="application/json",
        )

    columns: list[TableColumn] = []
    for item in resp.json().get("items", []):
        campo = item.get("x3_campo", "").strip()
        tipo = item.get("x3_tipo", "").strip()
        if not campo or _is_excluded_column(campo, tipo):
            continue
        columns.append(
            TableColumn(
                campo=campo,
                titulo=item.get("x3_titulo", "").strip(),
                tipo=tipo,
                tamanho=int(item.get("x3_tamanho", 0)),
            )
        )

    return func.HttpResponse(
        TableColumnsResponse(tabela=table, colunas=columns).model_dump_json(),
        status_code=200,
        mimetype="application/json",
    )
