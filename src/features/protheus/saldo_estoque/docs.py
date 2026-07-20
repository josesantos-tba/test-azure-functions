"""Documentação OpenAPI do endpoint ``GET /saldo-estoque``."""

from src.utils.openapi import inline_refs
from src.utils.protheus import ERROR_SCHEMA

from .models import DEFAULT_LIMIT, MAX_ROWS, SaldoEstoqueResponse

DOCS = {
    "summary": "Saldo de estoque disponível por produto e armazém (SB2 x SB1)",
    "description": (
        "Retorna o saldo de estoque da filial **TBA** por produto e armazém, "
        "cruzando os saldos físicos (`SB2`) com o cadastro de produtos (`SB1`).\n\n"
        "Para cada produto/armazém são calculados:\n"
        "- `tipo`: classificação pelo prefixo do código — `30*` Produto Acabado, "
        "`1005*` Embalagem, `10*` Matéria-prima, demais Produto em Processo.\n"
        "- `saldo_disponivel`: `B2_QATU - B2_QEMP` (ou `B2_QATU` quando o "
        "empenho é negativo).\n"
        "- `tba_arm`: `filial + '-' + armazém` com o armazém em 2 dígitos "
        "(ex: `TBA01-01`).\n\n"
        "Filtros fixos da consulta: códigos iniciados em `10`, `30`, `11`, `20` "
        "ou `R30`; exclui os códigos `10080001`/`10080002`, a descrição "
        "`SIMULACAO PROTHEUS` e registros deletados; considera apenas filiais "
        "que contêm `TBA`.\n\n"
        "Internamente executa a **genericQuery** uma única vez com o SELECT "
        "(join `SB2010` x `SB1010` + filtros) embutido no `FromQry`, aliased "
        "como `SB2`; as colunas derivadas acima são calculadas pela função a "
        "partir das colunas reais retornadas:\n"
        "```\n"
        "tables=SB2,SB1\n"
        "fields=B2_FILIAL,B2_COD,B1_DESC,B2_LOCAL,B2_QEMP,B2_QATU\n"
        "pagesize=<limit+1>\n"
        "FromQry=(SELECT T0.B2_FILIAL, T0.B2_COD, T1.B1_DESC, ... "
        "FROM SB2010 T0 INNER JOIN SB1010 T1 ON T1.B1_COD = T0.B2_COD ... "
        "ORDER BY T0.B2_FILIAL, T0.B2_COD, T0.B2_LOCAL, T0.R_E_C_N_O_ "
        "OFFSET <offset> ROWS FETCH NEXT <limit+1> ROWS ONLY) SB2\n"
        "FilialFilter=false\n"
        "DeletedFilter=false\n"
        "```\n\n"
        "### Paginação (limit + offset)\n"
        f"Os registros vêm ordenados por filial, código e armazém. Use `limit` "
        f"(padrão {DEFAULT_LIMIT}, máx. {MAX_ROWS}) e `offset` (padrão 0) para "
        "navegar: a primeira página é `offset=0`; as demais usam o `next_offset` "
        "devolvido na resposta (`offset + limit`), que vem `null` quando não há "
        "próxima página. Internamente é buscada uma linha a mais que `limit` "
        "(`OFFSET ... FETCH NEXT limit+1 ROWS ONLY`) para calcular `has_next` "
        "sem uma segunda consulta.\n\n"
        "### Download em CSV (`format=csv`)\n"
        "Com `format=csv` a resposta vem como **arquivo CSV** (separador vírgula, "
        "UTF-8 com BOM para abrir corretamente no Excel, cabeçalho com os nomes "
        "do relatório original: `Filial`, `Tipo`, `Codigo`, `Descricao`, "
        "`Armazem`, `QtdEmpenhada`, `SaldoDisponivel`, `SaldoAtual`, `TBA-ARM`) "
        "com `Content-Disposition` de download (`saldo-estoque.csv`). No CSV o "
        f"`limit` padrão é o máximo ({MAX_ROWS}), para o download vir completo; "
        "`limit`/`offset` continuam valendo se informados. Os headers "
        "`X-Row-Count` e `X-Has-Next` trazem a quantidade de linhas e se há "
        "mais registros além do recorte.\n\n"
        "Exemplos de chamada:\n"
        "```\n"
        "GET /api/saldo-estoque?limit=100&offset=200\n"
        "GET /api/saldo-estoque?format=csv\n"
        "```"
    ),
    "tags": ["Protheus"],
    "method": "get",
    "parameters": [
        {
            "name": "limit",
            "in": "query",
            "required": False,
            "schema": {
                "type": "integer",
                "default": DEFAULT_LIMIT,
                "maximum": MAX_ROWS,
                "example": 100,
            },
            "description": f"Linhas por página (máx. {MAX_ROWS})",
        },
        {
            "name": "offset",
            "in": "query",
            "required": False,
            "schema": {"type": "integer", "default": 0, "example": 200},
            "description": (
                "Quantidade de linhas puladas antes da página: use 0 (padrão) para a "
                "primeira página e o `next_offset` da resposta anterior para navegar."
            ),
        },
        {
            "name": "format",
            "in": "query",
            "required": False,
            "schema": {
                "type": "string",
                "enum": ["json", "csv"],
                "default": "json",
                "example": "csv",
            },
            "description": (
                "Formato da resposta: `json` (padrão) ou `csv` (download do "
                f"relatório; `limit` padrão vira {MAX_ROWS})."
            ),
        },
    ],
    "response": {
        200: {
            "description": "Saldos retornados com sucesso",
            "content": {
                "application/json": {
                    "schema": inline_refs(SaldoEstoqueResponse.model_json_schema()),
                    "example": {
                        "limit": 100,
                        "offset": 0,
                        "count": 1,
                        "has_next": True,
                        "next_offset": 100,
                        "items": [
                            {
                                "filial": "TBA01",
                                "tipo": "Matéria-prima",
                                "codigo": "10010001",
                                "descricao": "FARINHA DE TRIGO",
                                "armazem": "01",
                                "qtd_empenhada": 150.0,
                                "saldo_disponivel": 850.0,
                                "saldo_atual": 1000.0,
                                "tba_arm": "TBA01-01",
                            },
                        ],
                    },
                },
                "text/csv": {
                    "schema": {"type": "string"},
                    "example": (
                        "Filial,Tipo,Codigo,Descricao,Armazem,QtdEmpenhada,"
                        "SaldoDisponivel,SaldoAtual,TBA-ARM\r\n"
                        "TBA01,Matéria-prima,10010001,FARINHA DE TRIGO,01,"
                        "150.0,850.0,1000.0,TBA01-01\r\n"
                    ),
                },
            },
        },
        400: {
            "description": "Parâmetro 'limit', 'offset' ou 'format' inválido",
            "content": {
                "application/json": {
                    "schema": ERROR_SCHEMA,
                    "example": {"error": "'limit' deve ser um inteiro >= 1"},
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
            "description": "Usuário autenticado sem a role 'Reports.Read'",
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
    "operation_id": "saldoEstoque",
}
