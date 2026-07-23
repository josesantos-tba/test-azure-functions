"""Documentação OpenAPI do endpoint ``GET /table-config``."""

from src.utils.openapi import inline_refs
from src.utils.protheus import ERROR_SCHEMA
from src.utils.protheus_filters import FILTERS_REFERENCE_MD

from .models import TableConfigResponse

DOCS = {
    "summary": "Configuração de governança de uma tabela",
    "description": (
        "Retorna as regras de governança configuradas para uma tabela do Protheus "
        "(`src/config/table_config.json`), permitindo ao cliente saber, **antes de "
        "consultar/exportar**, o que o servidor vai aplicar:\n\n"
        "- **habilitada** — se a tabela pode ser consultada nos demais endpoints;\n"
        "- **csv_export_habilitado** — se a exportação CSV está liberada;\n"
        "- **filtro_obrigatorio** — filtros (mesmo formato do `filters` do `query-json`, "
        "podendo ser vários) sempre combinados (AND) com os filtros do cliente;\n"
        "- **colunas_obrigatorias** — colunas sempre incluídas em `fields`;\n"
        "- **colunas_permitidas** — whitelist de colunas (`null` = todas são permitidas);\n"
        "- **limite_max_linhas** — teto de linhas próprio da tabela (`null` = limite global).\n\n"
        "Quando a tabela não tem uma entrada própria, os valores retornados são os do "
        "`_default` e `possui_configuracao_especifica` vem `false`. Uma tabela "
        "desabilitada **não** retorna 403 aqui — a resposta mostra `habilitada: false`.\n\n"
        "### Formato do `filtro_obrigatorio`\n"
        "É um array JSON no mesmo formato do parâmetro `filters` do `query-json`.\n\n"
        + FILTERS_REFERENCE_MD
    ),
    "tags": ["Protheus"],
    "method": "get",
    "parameters": [
        {
            "name": "table",
            "in": "query",
            "required": True,
            "schema": {"type": "string", "example": "SE5"},
            "description": "Alias da tabela no Protheus (ex: SE5, SA1, SB1)",
        }
    ],
    "response": {
        200: {
            "description": "Configuração retornada com sucesso",
            "content": {
                "application/json": {
                    "schema": inline_refs(TableConfigResponse.model_json_schema()),
                    "example": {
                        "tabela": "SE5",
                        "possui_configuracao_especifica": True,
                        "habilitada": True,
                        "csv_export_habilitado": False,
                        "filtro_obrigatorio": [
                            {"column": "E5_TIPO", "operator": "=", "value": "VL", "type": "string"},
                            {"column": "E5_VALOR", "operator": ">", "value": 0, "type": "number"},
                        ],
                        "colunas_obrigatorias": ["E5_FILIAL", "E5_NUM", "E5_DATA"],
                        "colunas_permitidas": [
                            "E5_FILIAL", "E5_NUM", "E5_DATA", "E5_VALOR", "E5_TIPO", "E5_BANCO"
                        ],
                        "limite_max_linhas": 5000,
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
    },
    "operation_id": "getTableConfig",
}
