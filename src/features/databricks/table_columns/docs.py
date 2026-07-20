"""Documentação OpenAPI do endpoint ``GET /databricks/table-columns``."""

from src.utils.databricks import ERROR_SCHEMA
from src.utils.openapi import inline_refs

from .models import DatabricksColumnsResponse

DOCS = {
    "summary": "Colunas de uma tabela do Databricks",
    "description": (
        "Lista as colunas de uma tabela no Databricks (Unity Catalog / SQL "
        "Warehouse), consultando ``information_schema.columns``.\n\n"
        "Internamente executa:\n"
        "```sql\n"
        "SELECT column_name, data_type, ordinal_position, is_nullable, comment\n"
        "FROM <catalog>.information_schema.columns\n"
        "WHERE table_schema = :schema AND table_name = :table\n"
        "ORDER BY ordinal_position\n"
        "```"
    ),
    "tags": ["Databricks"],
    "method": "get",
    "parameters": [
        {
            "name": "table",
            "in": "query",
            "required": True,
            "description": "Nome da tabela cujas colunas serão listadas.",
            "schema": {"type": "string", "example": "clientes"},
        },
    ],
    "response": {
        200: {
            "description": "Lista de colunas retornada com sucesso",
            "content": {
                "application/json": {
                    "schema": inline_refs(
                        DatabricksColumnsResponse.model_json_schema(by_alias=True)
                    ),
                    "example": {
                        "catalogo": "main",
                        "schema": "default",
                        "tabela": "clientes",
                        "colunas": [
                            {
                                "nome": "id",
                                "tipo": "BIGINT",
                                "posicao": 1,
                                "nullable": False,
                                "comentario": "Identificador do cliente",
                            },
                        ],
                    },
                }
            },
        },
        400: {
            "description": "Parâmetro obrigatório ausente ou inválido",
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
    "operation_id": "databricksTableColumns",
}
