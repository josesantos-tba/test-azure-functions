"""Documentação OpenAPI do endpoint ``GET /query-json``."""

from src.utils.openapi import inline_refs
from src.utils.protheus import ERROR_SCHEMA
from src.utils.protheus_filters import FILTERS_REFERENCE_MD, filters_openapi_param

from .models import DEFAULT_PAGESIZE, MAX_PAGESIZE, TABLE_SUFFIX, QueryResponse

DOCS = {
    "summary": "Consulta em tabela do Protheus (JSON) com paginação por recno e filtros dinâmicos",
    "description": (
        "Executa uma consulta parametrizável em qualquer tabela do Protheus via **genericQuery** "
        "e retorna os dados em **JSON**.\n\n"
        "A tabela é informada pelo **alias** (ex: `CTK`, `SB1`); o nome físico é montado "
        f"com o sufixo `{TABLE_SUFFIX}` (ex: `CTK` → `CTK{TABLE_SUFFIX}`).\n\n"
        "Internamente a genericQuery é chamada **uma única vez** com os parâmetros no "
        "**header** — o WHERE (cursor de recno, deleção e filtros) fica embutido no "
        "`FromQry`, sem usar o parâmetro `where` do Protheus. Como a genericQuery aceita "
        "múltiplos aliases em `tables`, dados e cursores de paginação vêm na mesma "
        "resposta: cada linha carrega o próprio `R_E_C_N_O_` na coluna emprestada "
        "`A10_PRAZO` e o cursor da página anterior em `E5_VALOR` (colunas numéricas de "
        "tabelas padrão, removidas dos itens antes da resposta). Exemplo de segunda página:\n"
        "```\n"
        "tables=CTK,A10,SE5\n"
        "fields=CTK_FILIAL,CTK_CODFOR,CTK_CODCLI,CTK_SEQUEN,A10_PRAZO,E5_VALOR\n"
        "pagesize=51\n"
        f"FromQry=(SELECT T.*, T.R_E_C_N_O_ AS A10_PRAZO, (SELECT MAX(R_E_C_N_O_) FROM "
        f"(SELECT R_E_C_N_O_ FROM CTK{TABLE_SUFFIX} WHERE R_E_C_N_O_ > 21410710 AND "
        "D_E_L_E_T_ <> '*' ORDER BY R_E_C_N_O_ ASC FETCH NEXT 50 ROWS ONLY) HAVING "
        f"COUNT(*) >= 50) AS E5_VALOR FROM CTK{TABLE_SUFFIX} T WHERE "
        "R_E_C_N_O_ < 21410710 AND D_E_L_E_T_ <> '*' ORDER BY T.R_E_C_N_O_ DESC "
        "FETCH NEXT 51 ROWS ONLY) CTK\n"
        "FilialFilter=false\n"
        "DeletedFilter=false\n"
        "```\n\n"
        "### Paginação (recno + pagesize)\n"
        "Os registros vêm ordenados por `R_E_C_N_O_` **decrescente** — os mais recentes "
        "aparecem primeiro. A primeira página usa `recno=0` (padrão); as demais usam os "
        "cursores devolvidos na resposta:\n"
        "- `next_recno`: informe em `recno` para buscar a **próxima** página (registros "
        "mais antigos). `null` quando não há próxima página.\n"
        "- `previous_recno`: informe em `recno` para **voltar** à página anterior "
        "(registros mais recentes). `0` indica que a anterior é a primeira página; "
        "`null` indica que esta já é a primeira.\n\n"
        "Como o Protheus não retorna a coluna `R_E_C_N_O_` nos itens, o recno de cada "
        "linha viaja numa coluna numérica emprestada de outra tabela (artifício parecido "
        "com o da `A10` usado no `table-count`), o que permite calcular os cursores sem "
        "uma segunda chamada. Não há retorno do total de registros — "
        "use o endpoint `table-count` (com os mesmos `filters`) se precisar do total.\n\n"
        "### Filtros dinâmicos (`filters`)\n"
        "`filters` é um **array JSON** de objetos.\n\n"
        + FILTERS_REFERENCE_MD
        + "\n\n"
        "### Governança por tabela\n"
        "A tabela pode ter regras de configuração aplicadas pelo servidor "
        "(`src/config/table_config.json`), independentes do que o cliente enviar:\n"
        "- **tabela desabilitada** → resposta `403`;\n"
        "- **filtro obrigatório** sempre combinado (AND) com os `filters` do cliente;\n"
        "- **colunas obrigatórias** sempre incluídas em `fields`;\n"
        "- **colunas permitidas** (whitelist): pedir uma coluna fora dela → `400`;\n"
        "- **limite máximo de linhas** próprio da tabela, respeitado no `pagesize`.\n\n"
        "Exemplo de chamada:\n"
        "```\n"
        "GET /api/query-json"
        "?table=SE5"
        "&fields=E5_FILIAL,E5_NUMERO,E5_DATA,E5_VALOR"
        "&filters=" + '[{"column":"E5_VALOR","operator":">=","value":1000,"type":"number"},'
        '{"column":"E5_NUMERO","operator":"starts_with","value":"0001"},'
        '{"column":"E5_DATA","operator":"between","value":["2025-01-01","2025-01-31"],"type":"date"}]'
        + "&recno=0"
        "&pagesize=50"
        "```"
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
            "description": "Colunas desejadas separadas por vírgula",
        },
        filters_openapi_param(
            '[{"column":"E5_VALOR","operator":">=","value":1000,"type":"number"},'
            '{"column":"E5_NUMERO","operator":"starts_with","value":"0001"}]'
        ),
        {
            "name": "recno",
            "in": "query",
            "required": False,
            "schema": {"type": "integer", "default": 0, "example": 0},
            "description": (
                "Cursor de paginação: use 0 (padrão) para a primeira página e o "
                "`next_recno`/`previous_recno` da resposta anterior para navegar."
            ),
        },
        {
            "name": "pagesize",
            "in": "query",
            "required": False,
            "schema": {"type": "integer", "default": DEFAULT_PAGESIZE, "example": 50},
            "description": f"Quantidade de registros por página (máx. {MAX_PAGESIZE})",
        },
    ],
    "response": {
        200: {
            "description": "Dados retornados com sucesso",
            "content": {
                "application/json": {
                    "schema": inline_refs(QueryResponse.model_json_schema()),
                    "example": {
                        "pagesize": 50,
                        "previous_recno": 0,
                        "next_recno": 123406,
                        "items": [
                            {
                                "e5_filial": "01",
                                "e5_numero": "000001",
                                "e5_data": "2025-01-15",
                                "e5_valor": 1500.0,
                            },
                        ],
                    },
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
    "operation_id": "queryJson",
}
