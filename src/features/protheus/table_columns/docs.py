"""Documentação OpenAPI do endpoint ``GET /table-columns``."""

from src.utils.openapi import inline_refs
from src.utils.protheus import ERROR_SCHEMA

from .models import TableColumnsResponse

DOCS = {
    "summary": "Colunas de uma tabela Protheus",
    "description": (
        "Consulta o dicionário de campos **SX3** (tabela física `SX3010`, clonada em "
        "um banco **Azure SQL**) e retorna as colunas (campo, título, tipo e tamanho) "
        "da tabela informada.\n\n"
        "Internamente executa:\n"
        "```sql\n"
        "SELECT X3_CAMPO, X3_TITULO, X3_TIPO, X3_TAMANHO\n"
        "FROM SX3010\n"
        "WHERE D_E_L_E_T_ = ' ' AND X3_ARQUIVO = '{table}'\n"
        "ORDER BY X3_ORDEM\n"
        "```\n\n"
        "Campos de controle/log (sufixos `_USERLGI` e `_USERLGA`) e campos do tipo "
        "Memo (`M`) são omitidos, mantendo o contrato original do endpoint."
    ),
    "tags": ["Protheus"],
    "method": "get",
    "parameters": [
        {
            "name": "table",
            "in": "query",
            "required": True,
            "schema": {"type": "string", "example": "SE5"},
            "description": "Nome da tabela no dicionário do Protheus (ex: SE5, SA1)",
        }
    ],
    "response": {
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
            "description": "Falha ao conectar ou consultar o banco de dados (Azure SQL)",
            "content": {
                "application/json": {
                    "schema": ERROR_SCHEMA,
                    "example": {"error": "Falha ao consultar o banco de dados"},
                }
            },
        },
    },
    "operation_id": "getTableColumns",
}
