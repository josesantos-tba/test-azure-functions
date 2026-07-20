"""Handler do endpoint ``GET /saldo-estoque`` — saldo por produto/armazém."""

import csv
import io
import logging
from typing import Any

import azure.functions as func
import requests

from azure_functions_openapi import openapi

from src.utils.auth import require_roles
from src.utils.protheus import GENERIC_QUERY_URL, json_error, protheus_auth

from .docs import DOCS
from .models import DEFAULT_LIMIT, MAX_ROWS, SaldoEstoqueResponse

bp = func.Blueprint()

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

# Colunas do CSV: chave do item -> cabeçalho com os nomes do relatório original.
_CSV_COLUMNS = [
    ("filial", "Filial"),
    ("tipo", "Tipo"),
    ("codigo", "Codigo"),
    ("descricao", "Descricao"),
    ("armazem", "Armazem"),
    ("qtd_empenhada", "QtdEmpenhada"),
    ("saldo_disponivel", "SaldoDisponivel"),
    ("saldo_atual", "SaldoAtual"),
    ("tba_arm", "TBA-ARM"),
]


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


@openapi(**DOCS)
@bp.route(route="saldo-estoque", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
@require_roles("Reports.Read")
def saldo_estoque(req: func.HttpRequest) -> func.HttpResponse:

    fmt = req.params.get("format", "json").strip().lower()
    if fmt not in ("json", "csv"):
        return json_error("'format' deve ser 'json' ou 'csv'", 400)

    # No CSV o padrão é o relatório completo; no JSON, uma página de
    # DEFAULT_LIMIT registros.
    default_limit = MAX_ROWS if fmt == "csv" else DEFAULT_LIMIT
    limit_raw = req.params.get("limit", "").strip()
    try:
        limit = int(limit_raw) if limit_raw else default_limit
    except ValueError:
        limit = 0
    if limit < 1:
        return json_error("'limit' deve ser um inteiro >= 1", 400)
    limit = min(limit, MAX_ROWS)

    offset_raw = req.params.get("offset", "").strip()
    try:
        offset = int(offset_raw) if offset_raw else 0
    except ValueError:
        offset = -1
    if offset < 0:
        return json_error("'offset' deve ser um inteiro >= 0", 400)

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
            GENERIC_QUERY_URL,
            auth=protheus_auth(),
            timeout=60 * 10,
            headers=headers,
        )
    except requests.RequestException as exc:
        logging.error("Protheus saldoEstoque failed: %s", exc)
        return json_error("Falha ao conectar à API do Protheus", 502)

    if resp.status_code >= 400:
        logging.error(
            "Protheus saldoEstoque erro HTTP %s: %.500s", resp.status_code, resp.text
        )
        return json_error(f"Protheus retornou erro HTTP {resp.status_code}.", 502)

    try:
        data = resp.json()
    except ValueError:
        logging.error(
            "Protheus saldoEstoque retornou resposta não-JSON (status=%s): %.500s",
            resp.status_code,
            resp.text,
        )
        return json_error("Resposta inválida do Protheus (não-JSON).", 502)

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
                "Content-Disposition": 'attachment; filename="saldo-estoque.csv"',
                "X-Row-Count": str(len(items)),
                "X-Has-Next": str(has_next).lower(),
            },
        )

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
