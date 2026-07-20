"""Documentação OpenAPI do endpoint ``GET /databricks/table-count``."""

from src.utils.databricks import ERROR_SCHEMA
from src.utils.databricks.filters import filters_openapi_param
from src.utils.openapi import inline_refs

from .models import TableCountResponse

DOCS = {
    "summary": "Quantidade de registros de uma tabela do Databricks",
    "description": (
        "Retorna a quantidade de registros de uma tabela do Databricks (Unity "
        "Catalog / SQL Warehouse).\n\n"
        "A tabela é resolvida dentro do **catálogo** e **schema** configurados no "
        "ambiente (`DATABRICKS_CATALOG` / `DATABRICKS_SCHEMA`) — apenas o nome da "
        "tabela é informado.\n\n"
        "Aceita os mesmos **filtros dinâmicos** (`filters`) do endpoint "
        "`databricks/query-json`, permitindo contar apenas os registros que atendem "
        "às condições — útil para calcular o total de páginas de uma consulta "
        "filtrada.\n\n"
        "Internamente executa:\n"
        "```sql\n"
        "SELECT COUNT(*) AS total FROM <catalog>.<schema>.<table>\n"
        "[WHERE <filtros> ...]\n"
        "```\n"
        "Os valores dos filtros são passados como *bind parameters* (`?`), não "
        "interpolados na query."
    ),
    "tags": ["Databricks"],
    "method": "get",
    "parameters": [
        {
            "name": "table",
            "in": "query",
            "required": True,
            "schema": {"type": "string", "example": "clientes"},
            "description": "Nome da tabela (dentro do catalog/schema configurados).",
        },
        filters_openapi_param(
            '[{"column":"valor","operator":">=","value":1000,"type":"number"},'
            '{"column":"uf","operator":"=","value":"SP"}]'
        ),
    ],
    "response": {
        200: {
            "description": "Quantidade retornada com sucesso",
            "content": {
                "application/json": {
                    "schema": inline_refs(TableCountResponse.model_json_schema()),
                    "example": {"tabela": "clientes", "count": 12345},
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
            "description": "Usuário autenticado sem a role 'Tables.Read'",
            "content": {
                "application/json": {
                    "schema": ERROR_SCHEMA,
                    "example": {
                        "error": "Acesso negado: você não tem a permissão necessária "
                        "para acessar este recurso."
                    },
                }
            },
        },
        502: {
            "description": "Falha ao consultar o Databricks",
            "content": {
                "application/json": {
                    "schema": ERROR_SCHEMA,
                    "example": {"error": "Falha ao consultar o Databricks"},
                }
            },
        },
        503: {
            "description": "Conexão com o Databricks não configurada",
            "content": {
                "application/json": {
                    "schema": ERROR_SCHEMA,
                    "example": {"error": "Conexão com o Databricks não configurada"},
                }
            },
        },
    },
    "operation_id": "databricksTableCount",
}
