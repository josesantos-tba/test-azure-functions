"""Documentação OpenAPI do endpoint ``GET /table-count``."""

from src.utils.openapi import inline_refs
from src.utils.protheus import ERROR_SCHEMA
from src.utils.protheus_filters import filters_openapi_param

from .models import TABLE_SUFFIX, TableCountResponse

DOCS = {
    "summary": "Quantidade de registros de uma tabela do Protheus",
    "description": (
        "Retorna a quantidade de registros não deletados de uma tabela do Protheus, "
        "informada pelo **alias** (ex: `CTK`, `SB1`). O nome físico é montado com o "
        f"sufixo `{TABLE_SUFFIX}` (ex: `CTK` → `CTK{TABLE_SUFFIX}`).\n\n"
        "Aceita os mesmos **filtros dinâmicos** (`filters`) do endpoint `query-json`, "
        "permitindo contar apenas os registros que atendem às condições — útil para "
        "calcular o total de páginas de uma consulta filtrada. O WHERE usado é o "
        "mesmo do `query-json` (`R_E_C_N_O_ > 0 AND D_E_L_E_T_ <> '*'` + filtros), "
        "então a contagem corresponde ao que a listagem retorna.\n\n"
        "Internamente executa a **genericQuery** passando os parâmetros no **header**:\n"
        "```\n"
        "tables=A10\n"
        "fields=A10_PRAZO\n"
        "pagesize=1\n"
        "FromQry=(SELECT '' AS A10_FILIAL,'' AS A10_CJETAP,'' AS A10_ETAPA,"
        "'' AS A10_ETDESC,'' AS A10_WKFLOW,(SELECT COUNT(*) FROM <tabela> "
        "WHERE R_E_C_N_O_ > 0 AND D_E_L_E_T_ <> '*' AND <filtros>) AS A10_PRAZO, "
        "1 AS R_E_C_N_O_,0 AS R_E_C_D_E_L_,"
        "' ' AS D_E_L_E_T_ FROM DUAL) A10\n"
        "FilialFilter=false\n"
        "```\n"
        "O `COUNT(*)` da tabela informada é devolvido pelo Protheus na coluna "
        "`a10_prazo`, que é usada como resposta.\n\n"
        "### Governança por tabela\n"
        "A tabela pode ter regras de configuração aplicadas pelo servidor "
        "(`src/config/table_config.json`): se estiver **desabilitada**, a resposta é "
        "`403`; havendo **filtro obrigatório**, ele é combinado (AND) com os `filters` "
        "do cliente, de modo que a contagem corresponda ao que a listagem retorna."
    ),
    "tags": ["Protheus"],
    "method": "get",
    "parameters": [
        {
            "name": "table",
            "in": "query",
            "required": True,
            "schema": {"type": "string", "example": "CTK"},
            "description": "Alias da tabela no Protheus (ex: CTK, SB1, SE5)",
        },
        filters_openapi_param(
            '[{"column":"E5_VALOR","operator":">=","value":1000,"type":"number"},'
            '{"column":"E5_NUMERO","operator":"starts_with","value":"0001"}]'
        ),
    ],
    "response": {
        200: {
            "description": "Quantidade retornada com sucesso",
            "content": {
                "application/json": {
                    "schema": inline_refs(TableCountResponse.model_json_schema()),
                    "example": {"table": "CTK", "count": 21410759},
                }
            },
        },
        400: {
            "description": "Parâmetro 'table' ausente/inválido ou 'filters' inválido",
            "content": {
                "application/json": {
                    "schema": ERROR_SCHEMA,
                    "example": {"error": "Parâmetro obrigatório ausente: table"},
                }
            },
        },
        401: {
            "description": "Requisição não autenticada (token do Entra ID ausente ou inválido)",
            "content": {
                "application/json": {
                    "schema": ERROR_SCHEMA,
                    "example": {"error": "Requisição não autenticada."},
                }
            },
        },
        403: {
            "description": (
                "Acesso negado por falta da role 'Tables.Read', ou porque a tabela "
                "não está habilitada na configuração"
            ),
            "content": {
                "application/json": {
                    "schema": ERROR_SCHEMA,
                    "example": {"error": "Tabela 'SE5' não está habilitada"},
                }
            },
        },
        502: {
            "description": "Falha ao conectar ou obter resposta da API do Protheus",
            "content": {
                "application/json": {
                    "schema": ERROR_SCHEMA,
                    "example": {"error": "Falha ao conectar à API do Protheus"},
                }
            },
        },
    },
    "operation_id": "tableCount",
}
