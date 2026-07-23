"""Documentação OpenAPI do endpoint ``GET /export-csv``."""

from src.utils.protheus import ERROR_SCHEMA
from src.utils.protheus_filters import filters_openapi_param

from .models import MAX_ROWS

DOCS = {
    "summary": "Exporta uma tabela do Protheus em CSV (até 5 mil linhas)",
    "description": (
        "Exporta os registros de uma tabela do Protheus em **CSV**, com no máximo "
        f"**{MAX_ROWS}** linhas por requisição. Aceita os mesmos parâmetros do "
        "endpoint `query-json` (`table`, `fields`, `filters`), porém **sem paginação** "
        "(`recno`/`pagesize`): o resultado vem inteiro em uma única resposta, na ordem "
        "natural retornada pelo banco de dados.\n\n"
        "A tabela é informada pelo **alias** (ex: `CTK`, `SB1`), que o próprio "
        "Protheus resolve para o nome físico via SX2.\n\n"
        "Internamente a **genericQuery** é chamada uma única vez com os parâmetros "
        f"**nativos** dela (`tables`, `fields`, `where`, `pagesize={MAX_ROWS}`), sem "
        "`FromQry` — o corte de linhas é feito pelo próprio `pagesize` e os filtros "
        "dinâmicos viram o parâmetro `where`; os filtros de filial e de registros "
        "deletados são os automáticos da API (`FilialFilter`/`DeletedFilter`):\n"
        "```\n"
        "tables=CTK\n"
        "fields=CTK_FILIAL,CTK_CODFOR,CTK_CODCLI,CTK_SEQUEN\n"
        f"pagesize={MAX_ROWS}\n"
        "where=CTK_FILIAL = 'TBA'\n"
        "FilialFilter=false\n"
        "```\n\n"
        "O CSV é retornado com separador vírgula, codificação UTF-8 (com BOM, para "
        "abrir corretamente no Excel) e cabeçalho com as colunas de `fields`, na ordem "
        "informada. O header `X-Row-Count` traz a quantidade de linhas de dados; se "
        f"vier igual a `limit` (padrão {MAX_ROWS}), pode haver registros não "
        "exportados — use o endpoint `table-count` (com os mesmos `filters`) para "
        "saber o total.\n\n"
        "Use `limit` (opcional) para exportar menos linhas que o máximo.\n\n"
        "### Governança por tabela\n"
        "A tabela pode ter regras de configuração aplicadas pelo servidor "
        "(`src/config/table_config.json`), independentes do que o cliente enviar:\n"
        "- **tabela desabilitada** ou **exportação CSV desabilitada** → resposta `403`;\n"
        "- **filtro obrigatório** sempre combinado (AND) com os `filters` do cliente;\n"
        "- **colunas obrigatórias** sempre incluídas em `fields`;\n"
        "- **colunas permitidas** (whitelist): pedir uma coluna fora dela → `400`;\n"
        "- **limite máximo de linhas** próprio da tabela, respeitado além do global.\n\n"
        "Exemplo de chamada:\n"
        "```\n"
        "GET /api/export-csv"
        "?table=SE5"
        "&fields=E5_FILIAL,E5_NUMERO,E5_DATA,E5_VALOR"
        "&filters=" + '[{"column":"E5_DATA","operator":"between",'
        '"value":["2025-01-01","2025-01-31"],"type":"date"}]'
        "\n```"
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
        },
        {
            "name": "fields",
            "in": "query",
            "required": True,
            "schema": {"type": "string", "example": "E5_FILIAL,E5_NUMERO,E5_DATA,E5_VALOR"},
            "description": "Colunas desejadas separadas por vírgula (na ordem do CSV)",
        },
        filters_openapi_param(
            '[{"column":"E5_VALOR","operator":">=","value":1000,"type":"number"},'
            '{"column":"E5_NUMERO","operator":"starts_with","value":"0001"}]'
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
            "description": f"Quantidade máxima de linhas exportadas (máx. {MAX_ROWS})",
        },
    ],
    "response": {
        200: {
            "description": "CSV gerado com sucesso",
            "content": {
                "text/csv": {
                    "schema": {"type": "string"},
                    "example": (
                        "E5_FILIAL,E5_NUMERO,E5_DATA,E5_VALOR\r\n"
                        "01,000001,2025-01-15,1500.0\r\n"
                    ),
                }
            },
        },
        400: {
            "description": (
                "Parâmetros obrigatórios ausentes/inválidos, ou coluna fora da "
                "whitelist da tabela (`colunas_permitidas`)"
            ),
            "content": {
                "application/json": {
                    "schema": ERROR_SCHEMA,
                    "example": {
                        "error": "Colunas não permitidas para a tabela 'SE5': E5_SECRETO"
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
            "description": (
                "Acesso negado por falta da role 'Tables.Read', ou por regra de "
                "configuração da tabela (tabela não habilitada, ou exportação CSV "
                "desabilitada)"
            ),
            "content": {
                "application/json": {
                    "schema": ERROR_SCHEMA,
                    "example": {
                        "error": "Exportação CSV desabilitada para a tabela 'SE5'"
                    },
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
    "operation_id": "exportCsv",
}
