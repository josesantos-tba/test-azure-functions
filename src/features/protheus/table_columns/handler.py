"""Handler do endpoint ``GET /table-columns`` — colunas de uma tabela (SX3)."""

import logging

import azure.functions as func
import requests

from azure_functions_openapi import openapi

from src.utils.auth import require_roles
from src.utils.protheus import GENERIC_QUERY_URL, json_error, protheus_auth

from .docs import DOCS
from .models import TableColumn, TableColumnsResponse

bp = func.Blueprint()

# Campos que fazem o genericQuery responder "no content" (corpo vazio) quando
# pedidos em 'fields' e por isso são omitidos da lista de colunas:
#   - sufixos de controle/log de acesso do Protheus (_USERLGI, _USERLGA);
#   - campos do tipo Memo ('M'), que a API não consegue serializar.
_EXCLUDED_COLUMN_SUFFIXES = ("_USERLGI", "_USERLGA","_USERGI","USERGA")
_EXCLUDED_COLUMN_TYPES = ("M",)


def _is_excluded_column(campo: str, tipo: str) -> bool:
    """Indica se o campo é interno/incompatível e quebra o genericQuery."""
    return campo.upper().endswith(_EXCLUDED_COLUMN_SUFFIXES) or tipo.upper() in _EXCLUDED_COLUMN_TYPES


@openapi(**DOCS)
@bp.route(route="table-columns", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
@require_roles("Tables.Read")
def get_table_columns(req: func.HttpRequest) -> func.HttpResponse:
    table = req.params.get("table", "").strip().upper()
    if not table:
        return json_error("Parâmetro obrigatório ausente: table", 400)

    params = {
        "tables": "SX3",
        "fields": "X3_CAMPO,X3_TITULO,X3_TIPO,X3_TAMANHO",
        "where": f"SX3.D_E_L_E_T_=' ' AND SX3.X3_ARQUIVO='{table}'",
        "page": "1",
        "pagesize": "1000",
    }

    headers = {
        "FilialFilter": "false"
    }

    try:
        resp = requests.get(
            GENERIC_QUERY_URL,
            params=params,
            auth=protheus_auth(),
            timeout=30,
            headers=headers
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        logging.error("Protheus request failed: %s", exc)
        return json_error("Falha ao conectar à API do Protheus", 502)

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
