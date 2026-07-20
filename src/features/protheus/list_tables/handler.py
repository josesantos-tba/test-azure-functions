"""Handler do endpoint ``GET /tables`` — lista as tabelas do Protheus (SX2)."""

import logging

import azure.functions as func
import requests

from azure_functions_openapi import openapi

from src.utils.auth import require_roles
from src.utils.protheus import GENERIC_QUERY_URL, json_error, protheus_auth

from .docs import DOCS
from .models import ProtheusTable, TablesResponse

bp = func.Blueprint()

# Filtro temporário: somente estas tabelas (nome físico) são retornadas.
_ALLOWED_TABLES = frozenset(
    {
        "SC5010", "SB1010", "SD2010", "C1P010", "CT1010", "CT2010", "CV3010",
        "DA1010", "NNR010", "SA1010", "SB2010", "SA2010", "SBM010", "SD1010",
        "SE1010", "SE2010", "SE5010", "SED010", "SF1010", "SF2010", "SG1010",
        "SN4010", "SX5010", "SX2010", "SX3010", "C00010", "CC0010", "CTT010",
        "SE4010", "SC7010", "SC1010", "SYS_USR", "SYS_GRP_FILIAL", "SC9010",
        "DAK010", "SY1010", "CTK010", "CT5010", "SYS_COMPANY", "TOTVS_AUDIT",
        "SB8010", "SCR010", "CTH010", "SCY010", "SC6010", "SD3010", "SD4010",
        "SBF010", "SB5010", "SBE010", "SA6010", "SEB010", "SEA010", "SF5010",
        "SC2010", "SB9010",
    }
)


def _is_allowed(chave: str) -> bool:
    return chave in _ALLOWED_TABLES or f"{chave}010" in _ALLOWED_TABLES


@openapi(**DOCS)
@bp.route(route="tables", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
@require_roles("Tables.Read")
def list_tables(req: func.HttpRequest) -> func.HttpResponse:
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
        if _is_allowed(item.get("x2_chave", "").strip())
    ]

    return func.HttpResponse(
        TablesResponse(tabelas=tables).model_dump_json(),
        status_code=200,
        mimetype="application/json",
    )
