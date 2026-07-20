"""Documentação OpenAPI do endpoint ``GET /databricks/query-json``."""

from src.utils.databricks import ERROR_SCHEMA
from src.utils.databricks.filters import filters_openapi_param
from src.utils.openapi import inline_refs

from .models import DEFAULT_PAGESIZE, MAX_PAGESIZE, QueryResponse

DOCS = {
    "summary": "Consulta em tabela do Databricks (JSON) com paginação e filtros dinâmicos",
    "description": (
        "Executa uma consulta parametrizável em uma tabela do Databricks (Unity "
        "Catalog / SQL Warehouse) e retorna os dados em **JSON**.\n\n"
        "A tabela é resolvida dentro do **catálogo** e **schema** configurados no "
        "ambiente (`DATABRICKS_CATALOG` / `DATABRICKS_SCHEMA`) — apenas o nome da "
        "tabela é informado.\n\n"
        "Internamente monta:\n"
        "```sql\n"
        "SELECT <fields> FROM <catalog>.<schema>.<table>\n"
        "[WHERE <filtros> ...]\n"
        "LIMIT <pagesize + 1> OFFSET <(page - 1) * pagesize>\n"
        "```\n"
        "Os valores dos filtros são passados como *bind parameters* (`?`), não "
        "interpolados na query.\n\n"
        "### Paginação (page + pagesize)\n"
        "Paginação por deslocamento: `page` (1-based, padrão 1) e `pagesize` "
        f"(padrão {DEFAULT_PAGESIZE}, máx. {MAX_PAGESIZE}). A resposta traz "
        "`has_next` e `next_page`.\n\n"
        "### Filtros dinâmicos (`filters`)\n"
        "`filters` é um **array JSON** de objetos. Cada objeto aceita:\n"
        "- `column` (obrigatório): nome da coluna.\n"
        "- `operator` (obrigatório): um dos operadores abaixo.\n"
        "- `value`: valor comparado (dispensado em `em branco`/`nao em branco`).\n"
        "- `value2`: segundo valor, usado apenas no operador `entre` "
        "(ou informe `value` como lista `[inicio, fim]`).\n"
        "- `type` (opcional): `string` (padrão), `number` ou `date` (`YYYY-MM-DD`).\n\n"
        "Operadores suportados: `=`, `!=`, `>`, `<`, `<=`, `>=`, `contem`, "
        "`comeca com`, `termina em`, `entre`, `em branco`, `nao em branco`.\n\n"
        "Exemplo de chamada:\n"
        "```\n"
        "GET /api/databricks/query-json"
        "?table=clientes"
        "&fields=id,nome,uf,valor"
        "&filters=" + '[{"column":"valor","operator":">=","value":1000,"type":"number"},'
        '{"column":"uf","operator":"=","value":"SP"}]'
        + "&page=1"
        "&pagesize=50"
        "```"
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
            "description": "Colunas desejadas separadas por vírgula. Padrão: `*` (todas).",
        },
        filters_openapi_param(
            '[{"column":"valor","operator":">=","value":1000,"type":"number"},'
            '{"column":"uf","operator":"=","value":"SP"}]'
        ),
        {
            "name": "page",
            "in": "query",
            "required": False,
            "schema": {"type": "integer", "default": 1, "example": 1},
            "description": "Página (1-based).",
        },
        {
            "name": "pagesize",
            "in": "query",
            "required": False,
            "schema": {"type": "integer", "default": DEFAULT_PAGESIZE, "example": 50},
            "description": f"Quantidade de registros por página (máx. {MAX_PAGESIZE}).",
        },
    ],
    "response": {
        200: {
            "description": "Dados retornados com sucesso",
            "content": {
                "application/json": {
                    "schema": inline_refs(QueryResponse.model_json_schema()),
                    "example": {
                        "tabela": "clientes",
                        "page": 1,
                        "pagesize": 50,
                        "has_next": True,
                        "next_page": 2,
                        "items": [
                            {"id": 1, "nome": "ACME", "uf": "SP", "valor": 1500.0},
                        ],
                    },
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
    "operation_id": "databricksQueryJson",
}
