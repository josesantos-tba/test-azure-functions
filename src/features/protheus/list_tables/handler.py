"""Handler do endpoint ``GET /tables`` — lista as tabelas do Protheus (SX2).

Lê o dicionário de tabelas direto da tabela ``SX2010`` (clone do SX2 do Protheus)
em um banco **Azure SQL**, em vez de consultar o ``genericQuery`` do Protheus.
"""

import logging

import azure.functions as func

from azure_functions_openapi import openapi

from src.utils.auth import require_roles
from src.utils.azure_sql import AZURE_SQL_SCHEMA, azure_sql_config_ok, fetch_all, json_error

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

# Chaves (X2_CHAVE) equivalentes ao filtro acima: o antigo ``_is_allowed`` aceitava
# a chave quando ela — ou ``chave+"010"`` (nome físico) — estava em _ALLOWED_TABLES.
# Ordenado para manter a ordem estável entre os placeholders e os parâmetros.
_ALLOWED_KEYS = tuple(
    sorted(
        _ALLOWED_TABLES
        | {name[:-3] for name in _ALLOWED_TABLES if name.endswith("010")}
    )
)

# Consulta o dicionário SX2 (tabela física SX2010) já filtrando pelas tabelas
# permitidas, ignorando registros logicamente excluídos (D_E_L_E_T_). O schema
# vem de configuração (AZURE_SQL_SCHEMA), por isso é interpolado com segurança;
# as chaves permitidas entram como bind parameters (IN).
_QUERY = (
    "SELECT X2_CHAVE, X2_NOME, X2_MODO, X2_MODULO, X2_PYME "
    f"FROM {AZURE_SQL_SCHEMA}.SX2010 "
    "WHERE D_E_L_E_T_ = ' ' "
    f"AND X2_CHAVE IN ({', '.join('?' for _ in _ALLOWED_KEYS)})"
)


def _to_int(value: object) -> int:
    """Converte X2_MODULO (texto vindo do SQL) em int, tolerando vazio/espaços."""
    try:
        return int(str(value or "").strip() or 0)
    except ValueError:
        return 0


@openapi(**DOCS)
@bp.route(route="tables", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
@require_roles("Tables.Read")
def list_tables(req: func.HttpRequest) -> func.HttpResponse:
    if not azure_sql_config_ok():
        logging.error("Azure SQL não configurado (variáveis AZURE_SQL_* ausentes)")
        return json_error("Banco de dados não configurado", 502)

    try:
        rows = fetch_all(_QUERY, _ALLOWED_KEYS)
    except Exception as exc:  # noqa: BLE001 — falha de conexão/consulta ODBC
        logging.error("Azure SQL query failed: %s", exc)
        return json_error("Falha ao consultar o banco de dados", 502)

    tables = [
        ProtheusTable(
            chave=(item.get("X2_CHAVE") or "").strip(),
            nome=(item.get("X2_NOME") or "").strip(),
            modo=(item.get("X2_MODO") or "").strip(),
            modulo=_to_int(item.get("X2_MODULO")),
            pyme=(item.get("X2_PYME") or "").strip(),
        )
        for item in rows
    ]

    return func.HttpResponse(
        TablesResponse(tabelas=tables).model_dump_json(),
        status_code=200,
        mimetype="application/json",
    )
