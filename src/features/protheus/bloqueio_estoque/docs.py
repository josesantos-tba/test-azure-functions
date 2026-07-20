"""Documentação OpenAPI do endpoint ``GET /bloqueio-estoque``."""

from src.utils.openapi import inline_refs
from src.utils.protheus import ERROR_SCHEMA

from .models import DEFAULT_LIMIT, MAX_ROWS, BloqueioEstoqueResponse

DOCS = {
    "summary": "Bloqueio de estoque: saldos e lotes agregados dos produtos de uma "
    "carga, ordem de separação ou pedido (SB2 x SC9 x SB8)",
    "description": (
        "Relatório de **bloqueio de estoque**: retorna o saldo de estoque "
        "(`SB2`) e os saldos por lote (`SB8`, agregados por produto/armazém) "
        "dos produtos presentes nos itens liberados (`SC9`) que atendem ao "
        "filtro informado, permitindo identificar itens sem saldo disponível "
        "para faturamento.\n\n"
        "### Filtro (obrigatório, exatamente um)\n"
        "O usuário informa **exatamente um** dos parâmetros abaixo, aplicado "
        "sobre os itens liberados (`SC9`):\n"
        "- `carga` — código da carga (`C9_CARGA`);\n"
        "- `ordem_separacao` — ordem de separação (`C9_ORDSEP`);\n"
        "- `pedido` — número do pedido de venda (`C9_PEDIDO`).\n\n"
        "Os três campos são numéricos armazenados como **caracter de 6 "
        "posições** com zeros à esquerda (ex: `006407`). O valor informado "
        "deve conter apenas dígitos (até 6) e é completado automaticamente "
        "com zeros à esquerda antes da consulta — `carga=6407` e "
        "`carga=006407` retornam o mesmo resultado.\n\n"
        "Cada linha é um produto/armazém com: saldo atual (`B2_QATU`), "
        "quantidade reservada (`B2_RESERVA`), quantidade empenhada "
        "(`B2_QEMP`) e saldo disponível calculado como "
        "`B2_QATU - B2_RESERVA - B2_QEMP`; e os lotes (`SB8`) agregados via "
        "LEFT JOIN: quantidade de lotes (`COUNT(*)`), saldo somado "
        "(`SUM(B8_SALDO)`), empenho somado (`SUM(B8_EMPENHO)`), disponível "
        "dos lotes (`saldo_lote - empenhado_lote`) e a próxima validade "
        "(`MIN(B8_DTVALID)`; `null` quando o produto não controla lote — os "
        "agregados numéricos vêm `0`). Os registros vêm ordenados por saldo "
        "disponível **crescente** (os bloqueios aparecem primeiro).\n\n"
        "Internamente executa a **genericQuery** uma única vez com o SELECT "
        "(join `SB2010` x `SC9010` filtrada x `SB8010` agregada) embutido no "
        "`FromQry`, aliased como `SB2`; as colunas derivadas são calculadas "
        "pela função a partir das colunas reais retornadas — os agregados da "
        "SB8 são aliased para colunas reais do dicionário (`B8_QTDORI` "
        "carrega o `COUNT(*)` de lotes):\n"
        "```\n"
        "tables=SB2,SB8\n"
        "fields=B2_FILIAL,B2_COD,B2_LOCAL,B2_QATU,B2_RESERVA,B2_QEMP,"
        "B8_QTDORI,B8_SALDO,B8_EMPENHO,B8_DTVALID\n"
        "pagesize=<limit+1>\n"
        "FromQry=(SELECT T0.B2_FILIAL, ..., NVL(T2.QTD_LOTES, 0), ... "
        "FROM SB2010 T0 INNER JOIN (SELECT DISTINCT C9_FILIAL, C9_PRODUTO, "
        "C9_LOCAL FROM SC9010 WHERE <coluna do filtro> = '<valor>' AND "
        "D_E_L_E_T_ = ' ') T1 ON ... LEFT JOIN (SELECT B8_FILIAL, ..., "
        "COUNT(*), SUM(B8_SALDO), SUM(B8_EMPENHO), MIN(B8_DTVALID) "
        "FROM SB8010 WHERE D_E_L_E_T_ = ' ' GROUP BY ...) T2 ON ... "
        "ORDER BY (T0.B2_QATU - T0.B2_RESERVA - T0.B2_QEMP), T0.R_E_C_N_O_ "
        "OFFSET <offset> ROWS FETCH NEXT <limit+1> ROWS ONLY) SB2\n"
        "FilialFilter=false\n"
        "DeletedFilter=false\n"
        "```\n\n"
        "### Paginação (limit + offset)\n"
        f"Use `limit` (padrão {DEFAULT_LIMIT}, máx. {MAX_ROWS}) e `offset` "
        "(padrão 0) para navegar: a primeira página é `offset=0`; as demais "
        "usam o `next_offset` devolvido na resposta (`offset + limit`), que vem "
        "`null` quando não há próxima página. Internamente é buscada uma linha "
        "a mais que `limit` (`OFFSET ... FETCH NEXT limit+1 ROWS ONLY`) para "
        "calcular `has_next` sem uma segunda consulta.\n\n"
        "### Download em CSV (`format=csv`)\n"
        "Com `format=csv` a resposta vem como **arquivo CSV** (separador "
        "vírgula, UTF-8 com BOM para abrir corretamente no Excel, cabeçalho "
        "`Filial`, `Produto`, `Armazem`, `SaldoAtual`, `QtdReservada`, "
        "`QtdEmpenhada`, `SaldoDisponivel`, `QtdLotes`, `SaldoLote`, "
        "`EmpenhadoLote`, `DisponivelLote`, `ProximaValidade`) com "
        "`Content-Disposition` de download (`bloqueio-estoque.csv`). No CSV o "
        f"`limit` padrão é o máximo ({MAX_ROWS}), para o download vir "
        "completo; `limit`/`offset` continuam valendo se informados. Os "
        "headers `X-Row-Count` e `X-Has-Next` trazem a quantidade de linhas e "
        "se há mais registros além do recorte.\n\n"
        "Exemplos de chamada:\n"
        "```\n"
        "GET /api/bloqueio-estoque?carga=006407\n"
        "GET /api/bloqueio-estoque?ordem_separacao=123456\n"
        "GET /api/bloqueio-estoque?pedido=045123&format=csv\n"
        "```"
    ),
    "tags": ["Protheus"],
    "method": "get",
    "parameters": [
        {
            "name": "carga",
            "in": "query",
            "required": False,
            "schema": {"type": "string", "pattern": "^[0-9]{1,6}$", "example": "006407"},
            "description": (
                "Código da carga (`C9_CARGA`). Informe exatamente um dos "
                "filtros: `carga`, `ordem_separacao` ou `pedido`. Apenas "
                "dígitos (até 6); completado com zeros à esquerda (`6407` = "
                "`006407`)."
            ),
        },
        {
            "name": "ordem_separacao",
            "in": "query",
            "required": False,
            "schema": {"type": "string", "pattern": "^[0-9]{1,6}$", "example": "123456"},
            "description": (
                "Ordem de separação (`C9_ORDSEP`). Informe exatamente um dos "
                "filtros: `carga`, `ordem_separacao` ou `pedido`. Apenas "
                "dígitos (até 6); completado com zeros à esquerda (`6407` = "
                "`006407`)."
            ),
        },
        {
            "name": "pedido",
            "in": "query",
            "required": False,
            "schema": {"type": "string", "pattern": "^[0-9]{1,6}$", "example": "045123"},
            "description": (
                "Número do pedido de venda (`C9_PEDIDO`). Informe exatamente "
                "um dos filtros: `carga`, `ordem_separacao` ou `pedido`. "
                "Apenas dígitos (até 6); completado com zeros à esquerda "
                "(`6407` = `006407`)."
            ),
        },
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
                    "schema": inline_refs(BloqueioEstoqueResponse.model_json_schema()),
                    "example": {
                        "filtro": "carga",
                        "valor": "006407",
                        "limit": 100,
                        "offset": 0,
                        "count": 1,
                        "has_next": False,
                        "next_offset": None,
                        "items": [
                            {
                                "filial": "TBA01",
                                "produto": "30010001",
                                "armazem": "01",
                                "saldo_atual": 1000.0,
                                "qtd_reservada": 400.0,
                                "qtd_empenhada": 150.0,
                                "saldo_disponivel": 450.0,
                                "qtd_lotes": 3,
                                "saldo_lote": 1000.0,
                                "empenhado_lote": 150.0,
                                "disponivel_lote": 850.0,
                                "proxima_validade": "20261231",
                            },
                        ],
                    },
                },
                "text/csv": {
                    "schema": {"type": "string"},
                    "example": (
                        "Filial,Produto,Armazem,SaldoAtual,QtdReservada,"
                        "QtdEmpenhada,SaldoDisponivel,QtdLotes,SaldoLote,"
                        "EmpenhadoLote,DisponivelLote,ProximaValidade\r\n"
                        "TBA01,30010001,01,1000.0,400.0,150.0,450.0,"
                        "3,1000.0,150.0,850.0,20261231\r\n"
                    ),
                },
            },
        },
        400: {
            "description": (
                "Filtro ausente/duplicado/inválido ou parâmetro 'limit', "
                "'offset' ou 'format' inválido"
            ),
            "content": {
                "application/json": {
                    "schema": ERROR_SCHEMA,
                    "example": {
                        "error": "Informe exatamente um dos filtros: 'carga', "
                        "'ordem_separacao' ou 'pedido'"
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
    "operation_id": "bloqueioEstoque",
}
