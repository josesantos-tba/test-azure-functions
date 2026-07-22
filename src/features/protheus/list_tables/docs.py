"""Documentação OpenAPI do endpoint ``GET /tables``."""

from src.utils.openapi import inline_refs
from src.utils.protheus import ERROR_SCHEMA

from .models import TablesResponse

DOCS = {
    "summary": "Lista de tabelas do Protheus",
    "description": (
        "Consulta o dicionário de tabelas **SX2** (tabela física `SX2010`, clonada em "
        "um banco **Azure SQL**) e retorna as tabelas cadastradas (chave, nome, modo, "
        "módulo e indicador PYME).\n\n"
        "Internamente executa:\n"
        "```sql\n"
        "SELECT X2_CHAVE, X2_NOME, X2_MODO, X2_MODULO, X2_PYME\n"
        "FROM SX2010\n"
        "WHERE D_E_L_E_T_ = ' '\n"
        "```"
    ),
    "tags": ["Protheus"],
    "method": "get",
    "parameters": [],
    "response": {
        200: {
            "description": "Lista de tabelas retornada com sucesso",
            "content": {
                "application/json": {
                    "schema": inline_refs(TablesResponse.model_json_schema()),
                    "example": {
                        "tabelas": [
                            {
                                "chave": "A00",
                                "nome": "Território x Nível do Agrup.",
                                "modo": "C",
                                "modulo": 73,
                                "pyme": "S",
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
            "description": "Falha ao conectar ou consultar o banco de dados (Azure SQL)",
            "content": {
                "application/json": {
                    "schema": ERROR_SCHEMA,
                    "example": {"error": "Falha ao consultar o banco de dados"},
                }
            },
        },
    },
    "operation_id": "listTables",
}
