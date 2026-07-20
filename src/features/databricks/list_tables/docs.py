"""Documentação OpenAPI do endpoint ``GET /databricks/tables``."""

from src.utils.databricks import ERROR_SCHEMA
from src.utils.openapi import inline_refs

from .models import DatabricksTablesResponse

DOCS = {
    "summary": "Lista de tabelas do Databricks",
    "description": (
        "Lista as tabelas de um *schema* no Databricks (Unity Catalog / SQL "
        "Warehouse), consultando ``information_schema.tables``.\n\n"
        "Internamente executa:\n"
        "```sql\n"
        "SELECT table_catalog, table_schema, table_name, table_type\n"
        "FROM <catalog>.information_schema.tables\n"
        "WHERE table_schema = :schema\n"
        "ORDER BY table_name\n"
        "```"
    ),
    "tags": ["Databricks"],
    "method": "get",
    "parameters": [],
    "response": {
        200: {
            "description": "Lista de tabelas retornada com sucesso",
            "content": {
                "application/json": {
                    "schema": inline_refs(
                        DatabricksTablesResponse.model_json_schema(by_alias=True)
                    ),
                    "example": {
                        "catalogo": "main",
                        "schema": "default",
                        "tabelas": [
                            {
                                "catalogo": "main",
                                "schema": "default",
                                "nome": "dbo_clientes",
                                "display_name": "clientes",
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
        500: {
            "description": "Configuração de catálogo inválida no servidor",
            "content": {
                "application/json": {
                    "schema": ERROR_SCHEMA,
                    "example": {"error": "Configuração inválida: DATABRICKS_CATALOG"},
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
    "operation_id": "databricksListTables",
}
