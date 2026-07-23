"""Handler do endpoint ``GET /table-config`` — expõe a configuração de uma tabela.

Permite ao cliente saber quais regras de governança estão aplicadas a uma tabela
(filtro obrigatório, exportação CSV, colunas obrigatórias/permitidas, limite de
linhas e se está habilitada). Uma tabela desabilitada **não** dá 403 aqui: o
objetivo é justamente informar o estado da configuração, então a resposta mostra
``habilitada: false``.
"""

import azure.functions as func

from azure_functions_openapi import openapi

from src.utils.auth import require_roles
from src.utils.protheus import json_error
from src.utils.table_config import get_table_config, has_table_config

from .docs import DOCS
from .models import TableConfigResponse

bp = func.Blueprint()


@openapi(**DOCS)
@bp.route(route="table-config", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
@require_roles("Tables.Read")
def get_table_config_endpoint(req: func.HttpRequest) -> func.HttpResponse:
    table = req.params.get("table", "").strip().upper()
    if not table:
        return json_error("Parâmetro obrigatório ausente: table", 400)

    cfg = get_table_config(table)

    response = TableConfigResponse(
        tabela=table,
        possui_configuracao_especifica=has_table_config(table),
        **cfg.model_dump(),
    )

    return func.HttpResponse(
        response.model_dump_json(),
        status_code=200,
        mimetype="application/json",
    )
