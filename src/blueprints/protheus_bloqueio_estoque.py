import csv
import io
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

# FromQry com a consulta de bloqueio de estoque: saldos da SB2 restritos aos
# produtos/armazéns presentes em itens liberados (SC9). A subquery devolve apenas
# colunas reais do dicionário — saldo_disponivel é calculado em Python. As
# colunas do índice da SB2 (B2_FILIAL, B2_COD, B2_LOCAL) precisam existir na
# subquery mesmo que não sejam pedidas em 'fields': a genericQuery as
# referencia internamente e retorna 500 se faltarem.
_FROM_QRY = (
    "(SELECT "
    "T0.B2_FILIAL AS B2_FILIAL, "
    "T0.B2_COD AS B2_COD, "
    "T0.B2_LOCAL AS B2_LOCAL, "
    "T0.B2_QATU AS B2_QATU, "
    "T0.B2_RESERVA AS B2_RESERVA, "
    "T0.B2_QEMP AS B2_QEMP, "
    "T0.R_E_C_N_O_ AS R_E_C_N_O_, "
    "T0.R_E_C_D_E_L_ AS R_E_C_D_E_L_, "
    "T0.D_E_L_E_T_ AS D_E_L_E_T_ "
    "FROM SB2010 T0 "
    "INNER JOIN "
    "(SELECT DISTINCT C9_FILIAL, C9_PRODUTO, C9_LOCAL "
    "FROM SC9010) T1 "
    "ON T0.B2_FILIAL = T1.C9_FILIAL "
    "AND T0.B2_COD = T1.C9_PRODUTO "
    "AND T0.B2_LOCAL = T1.C9_LOCAL "
    "WHERE T0.D_E_L_E_T_ = ' ' "
    # Ordena pelo saldo disponível (mesma expressão calculada em Python) com
    # R_E_C_N_O_ como desempate: com OFFSET a ordenação precisa ser total,
    # senão linhas empatadas podem trocar de página entre requisições.
    "ORDER BY (T0.B2_QATU - T0.B2_RESERVA - T0.B2_QEMP), T0.R_E_C_N_O_ "
    "OFFSET {offset} ROWS FETCH NEXT {rows} ROWS ONLY"
    ") SB2"
)

# Colunas do CSV: chave do item -> cabeçalho com os nomes do relatório original.
_CSV_COLUMNS = [
    ("filial", "Filial"),
    ("codigo", "Codigo"),
    ("armazem", "Armazem"),
    ("saldo_atual", "SaldoAtual"),
    ("qtd_reservada", "QtdReservada"),
    ("qtd_empenhada", "QtdEmpenhada"),
    ("saldo_disponivel", "SaldoDisponivel"),
]

_ERROR_SCHEMA = {
    "type": "object",
    "properties": {"error": {"type": "string", "example": "Mensagem de erro"}},
    "required": ["error"],
}


class BloqueioEstoqueResponse(BaseModel):
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
            "Saldos de estoque dos produtos/armazéns com itens liberados, ordenados por "
            "saldo disponível crescente. Cada item tem as chaves: `filial`, "
            "`codigo`, `armazem`, `saldo_atual`, `qtd_reservada`, "
            "`qtd_empenhada` e `saldo_disponivel` "
            "(`saldo_atual - qtd_reservada - qtd_empenhada`)."
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


def _montar_item(row: dict[str, Any]) -> dict[str, Any]:
    saldo_atual = _to_float(row.get("b2_qatu"))
    qtd_reservada = _to_float(row.get("b2_reserva"))
    qtd_empenhada = _to_float(row.get("b2_qemp"))
    return {
        "filial": str(row.get("b2_filial", "")),
        "codigo": str(row.get("b2_cod", "")).strip(),
        "armazem": str(row.get("b2_local", "")),
        "saldo_atual": saldo_atual,
        "qtd_reservada": qtd_reservada,
        "qtd_empenhada": qtd_empenhada,
        "saldo_disponivel": saldo_atual - qtd_reservada - qtd_empenhada,
    }


@openapi(
    summary="Bloqueio de estoque: saldos dos produtos com itens liberados (SB2 x SC9)",
    description=(
        "Relatório de **bloqueio de estoque**: retorna o saldo de estoque "
        "(`SB2`) dos produtos/armazéns presentes nos itens liberados "
        "(`SC9`), permitindo identificar itens sem saldo disponível "
        "para faturamento.\n\n"
        "Para cada produto/armazém são retornados o saldo atual "
        "(`B2_QATU`), a quantidade reservada (`B2_RESERVA`), a quantidade "
        "empenhada (`B2_QEMP`) e o saldo disponível calculado como "
        "`B2_QATU - B2_RESERVA - B2_QEMP`. Os registros vêm ordenados por "
        "saldo disponível **crescente** (os bloqueios aparecem primeiro).\n\n"
        "Internamente executa a **genericQuery** uma única vez com o SELECT "
        "(join `SB2010` x `SC9010`) embutido no `FromQry`, "
        "aliased como `SB2`; o `saldo_disponivel` é calculado pela função a "
        "partir das colunas reais retornadas:\n"
        "```\n"
        "tables=SB2,SC9\n"
        "fields=B2_FILIAL,B2_COD,B2_LOCAL,B2_QATU,B2_RESERVA,B2_QEMP\n"
        "pagesize=<limit+1>\n"
        "FromQry=(SELECT T0.B2_FILIAL, T0.B2_COD, T0.B2_LOCAL, ... "
        "FROM SB2010 T0 INNER JOIN (SELECT DISTINCT C9_FILIAL, C9_PRODUTO, "
        "C9_LOCAL FROM SC9010) T1 ON ... "
        "ORDER BY (T0.B2_QATU - T0.B2_RESERVA - T0.B2_QEMP), T0.R_E_C_N_O_ "
        "OFFSET <offset> ROWS FETCH NEXT <limit+1> ROWS ONLY) SB2\n"
        "FilialFilter=false\n"
        "DeletedFilter=false\n"
        "```\n\n"
        "### Paginação (limit + offset)\n"
        f"Use `limit` (padrão {_DEFAULT_LIMIT}, máx. {_MAX_ROWS}) e `offset` "
        "(padrão 0) para navegar: a primeira página é `offset=0`; as demais "
        "usam o `next_offset` devolvido na resposta (`offset + limit`), que vem "
        "`null` quando não há próxima página. Internamente é buscada uma linha "
        "a mais que `limit` (`OFFSET ... FETCH NEXT limit+1 ROWS ONLY`) para "
        "calcular `has_next` sem uma segunda consulta.\n\n"
        "### Download em CSV (`format=csv`)\n"
        "Com `format=csv` a resposta vem como **arquivo CSV** (separador "
        "vírgula, UTF-8 com BOM para abrir corretamente no Excel, cabeçalho "
        "`Filial`, `Codigo`, `Armazem`, `SaldoAtual`, `QtdReservada`, "
        "`QtdEmpenhada`, `SaldoDisponivel`) com `Content-Disposition` de "
        "download (`bloqueio-estoque.csv`). No CSV o `limit` padrão é "
        f"o máximo ({_MAX_ROWS}), para o download vir completo; `limit`/"
        "`offset` continuam valendo se informados. Os headers `X-Row-Count` e "
        "`X-Has-Next` trazem a quantidade de linhas e se há mais registros "
        "além do recorte.\n\n"
        "Exemplos de chamada:\n"
        "```\n"
        "GET /api/bloqueio-estoque?limit=100&offset=200\n"
        "GET /api/bloqueio-estoque?format=csv\n"
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
                f"relatório; `limit` padrão vira {_MAX_ROWS})."
            ),
        },
    ],
    response={
        200: {
            "description": "Saldos retornados com sucesso",
            "content": {
                "application/json": {
                    "schema": inline_refs(BloqueioEstoqueResponse.model_json_schema()),
                    "example": {
                        "limit": 100,
                        "offset": 0,
                        "count": 1,
                        "has_next": False,
                        "next_offset": None,
                        "items": [
                            {
                                "filial": "TBA01",
                                "codigo": "30010001",
                                "armazem": "01",
                                "saldo_atual": 1000.0,
                                "qtd_reservada": 400.0,
                                "qtd_empenhada": 150.0,
                                "saldo_disponivel": 450.0,
                            },
                        ],
                    },
                },
                "text/csv": {
                    "schema": {"type": "string"},
                    "example": (
                        "Filial,Codigo,Armazem,SaldoAtual,QtdReservada,"
                        "QtdEmpenhada,SaldoDisponivel\r\n"
                        "TBA01,30010001,01,1000.0,400.0,150.0,450.0\r\n"
                    ),
                },
            },
        },
        400: {
            "description": "Parâmetro 'limit', 'offset' ou 'format' inválido",
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
            "description": "Usuário autenticado sem a role 'Reports.Read'",
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
    operation_id="bloqueioEstoque",
)
@bp.route(route="bloqueio-estoque", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
@require_roles("Reports.Read")
def bloqueio_estoque(req: func.HttpRequest) -> func.HttpResponse:

    fmt = req.params.get("format", "json").strip().lower()
    if fmt not in ("json", "csv"):
        return _json_error("'format' deve ser 'json' ou 'csv'", 400)

    # No CSV o padrão é o relatório completo; no JSON, uma página de
    # _DEFAULT_LIMIT registros.
    default_limit = _MAX_ROWS if fmt == "csv" else _DEFAULT_LIMIT
    limit_raw = req.params.get("limit", "").strip()
    try:
        limit = int(limit_raw) if limit_raw else default_limit
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
        "tables": "SB2,SC9",
        "fields": "B2_FILIAL,B2_COD,B2_LOCAL,B2_QATU,B2_RESERVA,B2_QEMP",
        # Uma linha a mais que a página para saber se há próxima página.
        "pagesize": str(limit + 1),
        "FromQry": _FROM_QRY.format(offset=offset, rows=limit + 1),
        "FilialFilter": "false",
        # O filtro de deleção já está embutido no FromQry; o automático
        # qualificaria D_E_L_E_T_ também no alias SC9, inexistente na subquery.
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
        logging.error("Protheus bloqueioEstoque failed: %s", exc)
        return _json_error("Falha ao conectar à API do Protheus", 502)

    if resp.status_code >= 400:
        logging.error(
            "Protheus bloqueioEstoque erro HTTP %s: %.500s", resp.status_code, resp.text
        )
        return _json_error(f"Protheus retornou erro HTTP {resp.status_code}.", 502)

    try:
        data = resp.json()
    except ValueError:
        logging.error(
            "Protheus bloqueioEstoque retornou resposta não-JSON (status=%s): %.500s",
            resp.status_code,
            resp.text,
        )
        return _json_error("Resposta inválida do Protheus (não-JSON).", 502)

    rows = data.get("items", [])
    has_next = len(rows) > limit
    items = [_montar_item(row) for row in rows[:limit]]

    if fmt == "csv":
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\r\n")
        writer.writerow([header for _, header in _CSV_COLUMNS])
        for item in items:
            writer.writerow(
                ["" if item[key] is None else item[key] for key, _ in _CSV_COLUMNS]
            )
        # BOM (utf-8-sig) para o Excel reconhecer a codificação UTF-8.
        return func.HttpResponse(
            buffer.getvalue().encode("utf-8-sig"),
            status_code=200,
            mimetype="text/csv",
            headers={
                "Content-Disposition": 'attachment; filename="bloqueio-estoque.csv"',
                "X-Row-Count": str(len(items)),
                "X-Has-Next": str(has_next).lower(),
            },
        )

    return func.HttpResponse(
        BloqueioEstoqueResponse(
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
