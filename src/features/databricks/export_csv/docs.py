"""Documentação OpenAPI do endpoint ``GET /databricks/export-csv``."""

from src.utils.databricks import ERROR_SCHEMA
from src.utils.databricks.filters import filters_openapi_param

from .models import MAX_ROWS

DOCS = {
    "summary": f"Exporta uma tabela do Databricks em CSV (até {MAX_ROWS} linhas)",
    "description": (
        "Exporta os registros de uma tabela do Databricks (Unity Catalog / SQL "
        f"Warehouse) em **CSV**, com no máximo **{MAX_ROWS}** linhas por requisição. "
        "Aceita os mesmos parâmetros do endpoint `databricks/query-json` (`table`, "
        "`fields`, `filters`), porém **sem paginação** (`page`/`pagesize`): o resultado "
        "vem inteiro em uma única resposta.\n\n"
        "A tabela é resolvida dentro do **catálogo** e **schema** configurados no "
        "ambiente (`DATABRICKS_CATALOG` / `DATABRICKS_SCHEMA`) — apenas o nome da "
        "tabela é informado.\n\n"
        "Internamente monta:\n"
        "```sql\n"
        "SELECT <fields> FROM <catalog>.<schema>.<table>\n"
        "[WHERE <filtros> ...]\n"
        f"LIMIT <limit (máx. {MAX_ROWS})>\n"
        "```\n"
        "Os valores dos filtros são passados como *bind parameters* (`?`), não "
        "interpolados na query.\n\n"
        "O CSV é retornado com separador vírgula, codificação UTF-8 (com BOM, para "
        "abrir corretamente no Excel) e cabeçalho com as colunas de `fields`, na ordem "
        "informada (ou todas as colunas quando `fields` é omitido). O header "
        "`X-Row-Count` traz a quantidade de linhas de dados; se vier igual a `limit`, "
        "pode haver registros não exportados.\n\n"
        "Exemplo de chamada:\n"
        "```\n"
        "GET /api/databricks/export-csv"
        "?table=clientes"
        "&fields=id,nome,uf,valor"
        "&filters=" + '[{"column":"uf","operator":"=","value":"SP"}]'
        "&limit=5000"
        "\n```"
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
        {
            "name": "fields",
            "in": "query",
            "required": False,
            "schema": {"type": "string", "example": "id,nome,uf,valor"},
            "description": "Colunas desejadas separadas por vírgula (na ordem do CSV). Padrão: `*` (todas).",
        },
        filters_openapi_param(
            '[{"column":"valor","operator":">=","value":1000,"type":"number"},'
            '{"column":"uf","operator":"=","value":"SP"}]'
        ),
        {
            "name": "limit",
            "in": "query",
            "required": False,
            "schema": {
                "type": "integer",
                "default": MAX_ROWS,
                "maximum": MAX_ROWS,
                "example": 5000,
            },
            "description": f"Quantidade máxima de linhas exportadas (máx. {MAX_ROWS}).",
        },
    ],
    "response": {
        200: {
            "description": "CSV gerado com sucesso",
            "content": {
                "text/csv": {
                    "schema": {"type": "string"},
                    "example": (
                        "id,nome,uf,valor\r\n"
                        "1,ACME,SP,1500.0\r\n"
                    ),
                }
            },
        },
        400: {
            "description": "Parâmetros obrigatórios ausentes ou inválidos",
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
    "operation_id": "databricksExportCsv",
}
