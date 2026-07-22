"""Handler do endpoint ``GET /table-columns`` — colunas de uma tabela (SX3).

Lê o dicionário de campos direto da tabela ``SX3010`` (clone do SX3 do Protheus)
em um banco **Azure SQL**, em vez de consultar o ``genericQuery`` do Protheus.
"""

import logging

import azure.functions as func

from azure_functions_openapi import openapi

from src.utils.auth import require_roles
from src.utils.azure_sql import AZURE_SQL_SCHEMA, azure_sql_config_ok, fetch_all, json_error

from .docs import DOCS
from .models import TableColumn, TableColumnsResponse

bp = func.Blueprint()

# Campos que fazem o genericQuery responder "no content" (corpo vazio) quando
# pedidos em 'fields' e por isso são omitidos da lista de colunas:
#   - sufixos de controle/log de acesso do Protheus (_USERLGI, _USERLGA);
#   - campos do tipo Memo ('M'), que a API não consegue serializar.
_EXCLUDED_COLUMN_SUFFIXES = ("_USERLGI", "_USERLGA", "_USERGI", "USERGA")
_EXCLUDED_COLUMN_TYPES = ("M",)

# Consulta o dicionário SX3 (tabela física SX3010) filtrando pela tabela alvo e
# ignorando registros logicamente excluídos (D_E_L_E_T_). O schema vem de
# configuração (AZURE_SQL_SCHEMA), por isso é interpolado com segurança.
_QUERY = (
    "SELECT X3_CAMPO, X3_TITULO, X3_TIPO, X3_TAMANHO "
    f"FROM {AZURE_SQL_SCHEMA}.SX3010 "
    "WHERE D_E_L_E_T_ = ' ' AND X3_ARQUIVO = ? "
    "ORDER BY X3_ORDEM"
)


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

    if not azure_sql_config_ok():
        logging.error("Azure SQL não configurado (variáveis AZURE_SQL_* ausentes)")
        return json_error("Banco de dados não configurado", 502)

    try:
        rows = fetch_all(_QUERY, [table])
    except Exception as exc:  # noqa: BLE001 — falha de conexão/consulta ODBC
        logging.error("Azure SQL query failed: %s", exc)
        return json_error("Falha ao consultar o banco de dados", 502)

    columns: list[TableColumn] = []
    for item in rows:
        campo = (item.get("X3_CAMPO") or "").strip()
        tipo = (item.get("X3_TIPO") or "").strip()
        if not campo or _is_excluded_column(campo, tipo):
            continue
        columns.append(
            TableColumn(
                campo=campo,
                titulo=(item.get("X3_TITULO") or "").strip(),
                tipo=tipo,
                tamanho=int(item.get("X3_TAMANHO") or 0),
            )
        )

    return func.HttpResponse(
        TableColumnsResponse(tabela=table, colunas=columns).model_dump_json(),
        status_code=200,
        mimetype="application/json",
    )
