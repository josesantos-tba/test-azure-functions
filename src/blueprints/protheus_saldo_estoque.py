import json
import logging
import os
from typing import Any

import azure.functions as func
import requests
from pydantic import BaseModel, Field

from azure_functions_openapi import openapi

from src.utils.auth import require_roles
from src.utils.openapi import inline_refs

bp = func.Blueprint()

_PROTHEUS_BASE_URL = os.environ.get("PROTHEUS_BASE_URL", "")
_PROTHEUS_AUTH = (
    os.environ.get("PROTHEUS_USER", ""),
    os.environ.get("PROTHEUS_PASSWORD", ""),
)

# Linhas por página (padrão e máximo).
_DEFAULT_LIMIT = 100
_MAX_ROWS = 10000

# FromQry com a consulta de saldo de estoque (SB2 x SB1). A subquery devolve
# apenas colunas reais do dicionário — as colunas derivadas (tipo,
# saldo_disponivel, tba_arm) são calculadas em Python, evitando literais com
# acento no header HTTP (onde o FromQry viaja). As colunas do índice da SB2
# (B2_FILIAL, B2_COD, B2_LOCAL) precisam existir na subquery mesmo que não
# sejam pedidas em 'fields': a genericQuery as referencia internamente e
# retorna 500 se faltarem.
_FROM_QRY = (
    "(SELECT "
    "T0.B2_FILIAL AS B2_FILIAL, "
    "T0.B2_COD AS B2_COD, "
    "T1.B1_DESC AS B1_DESC, "
    "T0.B2_LOCAL AS B2_LOCAL, "
    "T0.B2_QEMP AS B2_QEMP, "
    "T0.B2_QATU AS B2_QATU, "
    "T0.R_E_C_N_O_ AS R_E_C_N_O_, "
    "T0.R_E_C_D_E_L_ AS R_E_C_D_E_L_, "
    "T0.D_E_L_E_T_ AS D_E_L_E_T_ "
    "FROM SB2010 T0 "
    "INNER JOIN SB1010 T1 "
    "ON T1.B1_COD = T0.B2_COD "
    "AND T1.B1_FILIAL = 'TBA' "
    "AND T1.D_E_L_E_T_ <> '*' "
    "WHERE ("
    "TRIM(T0.B2_COD) LIKE '10%' "
    "OR TRIM(T0.B2_COD) LIKE '30%' "
    "OR TRIM(T0.B2_COD) LIKE '11%' "
    "OR TRIM(T0.B2_COD) LIKE '20%' "
    "OR TRIM(T0.B2_COD) LIKE 'R30%'"
    ") "
    "AND TRIM(T0.B2_COD) NOT IN ('10080001', '10080002') "
    "AND TRIM(T1.B1_DESC) <> 'SIMULACAO PROTHEUS' "
    "AND T0.B2_FILIAL LIKE '%TBA%' "
    "AND T0.D_E_L_E_T_ <> '*' "
    # Mesma ordem do índice (filial, código, armazém) que a genericQuery
    # aplica ao reordenar cada página — assim as páginas ficam contíguas.
    # R_E_C_N_O_ como desempate: com OFFSET a ordenação precisa ser total,
    # senão linhas empatadas podem trocar de página entre requisições.
    "ORDER BY T0.B2_FILIAL, T0.B2_COD, T0.B2_LOCAL, T0.R_E_C_N_O_ "
    "OFFSET {offset} ROWS FETCH NEXT {rows} ROWS ONLY"
    ") SB2"
)

_ERROR_SCHEMA = {
    "type": "object",
    "properties": {"error": {"type": "string", "example": "Mensagem de erro"}},
    "required": ["error"],
}


class SaldoEstoqueResponse(BaseModel):
    limit: int = Field(description="Linhas por página usadas na consulta.")
    offset: int = Field(description="Deslocamento (linhas puladas) usado na consulta.")
    count: int = Field(description="Quantidade de itens retornados nesta página.")
    has_next: bool = Field(description="Indica se existe próxima página.")
    next_offset: int | None = Field(
        description=(
            "Valor de `offset` para buscar a próxima página (`offset + limit`); "
            "`null` quando não há próxima página."
        )
    )
    items: list[dict[str, Any]] = Field(
        description=(
            "Saldos de estoque ordenados por filial, código e armazém. Cada item tem as "
            "chaves: `filial`, `tipo` (Produto Acabado, Embalagem, Matéria-prima "
            "ou Produto em Processo), `codigo`, `descricao`, `armazem`, "
            "`qtd_empenhada`, `saldo_disponivel`, `saldo_atual` e `tba_arm` "
            "(filial + '-' + armazém com 2 dígitos)."
        )
    )


def _json_error(message: str, status: int) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps({"error": message}),
        status_code=status,
        mimetype="application/json",
    )


def _to_float(value: Any) -> float:
    """Converte um valor numérico vindo do Protheus (número ou string) em float."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _classificar_tipo(codigo: str) -> str:
    """Classifica o produto pelo prefixo do código (mesma regra do CASE da consulta)."""
    if codigo.startswith("30"):
        return "Produto Acabado"
    if codigo.startswith("1005"):
        return "Embalagem"
    if codigo.startswith("10"):
        return "Matéria-prima"
    return "Produto em Processo"


def _montar_item(row: dict[str, Any]) -> dict[str, Any]:
    codigo = str(row.get("b2_cod", "")).strip()
    filial = str(row.get("b2_filial", ""))
    armazem = str(row.get("b2_local", ""))
    qtd_empenhada = _to_float(row.get("b2_qemp"))
    saldo_atual = _to_float(row.get("b2_qatu"))
    # Mesma semântica do LPAD(TRIM(...), 2, '0') do Oracle: completa à
    # esquerda com '0', mantém apenas os 2 primeiros caracteres e devolve
    # NULL (vazio na concatenação) quando o armazém é vazio.
    armazem_2dig = armazem.strip().zfill(2)[:2] if armazem.strip() else ""
    return {
        "filial": filial,
        "tipo": _classificar_tipo(codigo),
        "codigo": codigo,
        "descricao": row.get("b1_desc"),
        "armazem": armazem,
        "qtd_empenhada": qtd_empenhada,
        "saldo_disponivel": saldo_atual if qtd_empenhada < 0 else saldo_atual - qtd_empenhada,
        "saldo_atual": saldo_atual,
        "tba_arm": f"{filial}-{armazem_2dig}",
    }


@openapi(
    summary="Saldo de estoque disponível por produto e armazém (SB2 x SB1)",
    description=(
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
        f"(padrão {_DEFAULT_LIMIT}, máx. {_MAX_ROWS}) e `offset` (padrão 0) para "
        "navegar: a primeira página é `offset=0`; as demais usam o `next_offset` "
        "devolvido na resposta (`offset + limit`), que vem `null` quando não há "
        "próxima página. Internamente é buscada uma linha a mais que `limit` "
        "(`OFFSET ... FETCH NEXT limit+1 ROWS ONLY`) para calcular `has_next` "
        "sem uma segunda consulta.\n\n"
        "Exemplo de chamada:\n"
        "```\n"
        "GET /api/saldo-estoque?limit=100&offset=200\n"
        "```"
    ),
    tags=["Protheus"],
    method="get",
    parameters=[
        {
            "name": "limit",
            "in": "query",
            "required": False,
            "schema": {
                "type": "integer",
                "default": _DEFAULT_LIMIT,
                "maximum": _MAX_ROWS,
                "example": 100,
            },
            "description": f"Linhas por página (máx. {_MAX_ROWS})",
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
    ],
    response={
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
                }
            },
        },
        400: {
            "description": "Parâmetro 'limit' ou 'offset' inválido",
            "content": {
                "application/json": {
                    "schema": _ERROR_SCHEMA,
                    "example": {"error": "'limit' deve ser um inteiro >= 1"},
                }
            },
        },
        401: {
            "description": "Requisição não autenticada (token do Entra ID ausente ou inválido)",
            "content": {
                "application/json": {
                    "schema": _ERROR_SCHEMA,
                    "example": {"error": "Requisição não autenticada."},
                }
            },
        },
        403: {
            "description": "Usuário autenticado sem a role 'Tables.Read'",
            "content": {
                "application/json": {
                    "schema": _ERROR_SCHEMA,
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
                    "schema": _ERROR_SCHEMA,
                    "example": {"error": "Falha ao conectar à API do Protheus"},
                }
            },
        },
    },
    operation_id="saldoEstoque",
)
@bp.route(route="saldo-estoque", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
@require_roles("Tables.Read")
def saldo_estoque(req: func.HttpRequest) -> func.HttpResponse:

    limit_raw = req.params.get("limit", "").strip()
    try:
        limit = int(limit_raw) if limit_raw else _DEFAULT_LIMIT
    except ValueError:
        limit = 0
    if limit < 1:
        return _json_error("'limit' deve ser um inteiro >= 1", 400)
    limit = min(limit, _MAX_ROWS)

    offset_raw = req.params.get("offset", "").strip()
    try:
        offset = int(offset_raw) if offset_raw else 0
    except ValueError:
        offset = -1
    if offset < 0:
        return _json_error("'offset' deve ser um inteiro >= 0", 400)

    headers = {
        "tables": "SB2,SB1",
        "fields": "B2_FILIAL,B2_COD,B1_DESC,B2_LOCAL,B2_QEMP,B2_QATU",
        # Uma linha a mais que a página para saber se há próxima página.
        "pagesize": str(limit + 1),
        "FromQry": _FROM_QRY.format(offset=offset, rows=limit + 1),
        "FilialFilter": "false",
        # Os filtros de filial/deleção já estão embutidos no FromQry; os
        # automáticos qualificariam D_E_L_E_T_ também no alias SB1,
        # inexistente na subquery.
        "DeletedFilter": "false",
    }

    try:
        resp = requests.get(
            f"{_PROTHEUS_BASE_URL}/api/framework/v1/genericQuery",
            auth=_PROTHEUS_AUTH if all(_PROTHEUS_AUTH) else None,
            timeout=60 * 10,
            headers=headers,
        )
    except requests.RequestException as exc:
        logging.error("Protheus saldoEstoque failed: %s", exc)
        return _json_error("Falha ao conectar à API do Protheus", 502)

    if resp.status_code >= 400:
        logging.error(
            "Protheus saldoEstoque erro HTTP %s: %.500s", resp.status_code, resp.text
        )
        return _json_error(f"Protheus retornou erro HTTP {resp.status_code}.", 502)

    try:
        data = resp.json()
    except ValueError:
        logging.error(
            "Protheus saldoEstoque retornou resposta não-JSON (status=%s): %.500s",
            resp.status_code,
            resp.text,
        )
        return _json_error("Resposta inválida do Protheus (não-JSON).", 502)

    rows = data.get("items", [])
    has_next = len(rows) > limit
    items = [_montar_item(row) for row in rows[:limit]]

    return func.HttpResponse(
        SaldoEstoqueResponse(
            limit=limit,
            offset=offset,
            count=len(items),
            has_next=has_next,
            next_offset=offset + limit if has_next else None,
            items=items,
        ).model_dump_json(),
        status_code=200,
        mimetype="application/json",
    )
