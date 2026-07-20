"""Documentação OpenAPI do endpoint ``GET /query-json``."""

from src.utils.openapi import inline_refs
from src.utils.protheus import ERROR_SCHEMA
from src.utils.protheus_filters import filters_openapi_param

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
        "`filters` é um **array JSON** de objetos. Cada objeto aceita:\n"
        "- `column` (obrigatório): nome da coluna (ex: `E5_VALOR`).\n"
        "- `operator` (obrigatório): um dos operadores abaixo.\n"
        "- `value`: valor comparado (dispensado em `em branco`/`nao em branco`).\n"
        "- `value2`: segundo valor, usado apenas no operador `entre` "
        "(ou informe `value` como lista `[inicio, fim]`).\n"
        "- `type` (opcional): `string` (padrão), `number` ou `date` (`YYYY-MM-DD`).\n\n"
        "Operadores suportados: `=`, `!=`, `>`, `<`, `<=`, `>=`, `contem`, "
        "`comeca com`, `termina em`, `entre`, `em branco`, `nao em branco`.\n\n"
        "Exemplo de chamada:\n"
        "```\n"
        "GET /api/query-json"
        "?table=SE5"
        "&fields=E5_FILIAL,E5_NUMERO,E5_DATA,E5_VALOR"
        "&filters=" + '[{"column":"E5_VALOR","operator":">=","value":1000,"type":"number"},'
        '{"column":"E5_NUMERO","operator":"comeca com","value":"0001"},'
        '{"column":"E5_DATA","operator":"entre","value":["2025-01-01","2025-01-31"],"type":"date"}]'
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
            '{"column":"E5_NUMERO","operator":"comeca com","value":"0001"}]'
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
            "description": "Parâmetros obrigatórios ausentes ou inválidos",
            "content": {
                "application/json": {
                    "schema": ERROR_SCHEMA,
                    "example": {"error": "Parâmetros obrigatórios ausentes: table, fields"},
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
