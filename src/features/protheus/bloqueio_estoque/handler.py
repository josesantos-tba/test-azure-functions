"""Handler do endpoint ``GET /bloqueio-estoque`` — saldos e lotes por filtro."""

import csv
import io
import logging
import re
from typing import Any

import azure.functions as func
import requests

from azure_functions_openapi import openapi

from src.utils.auth import require_roles
from src.utils.protheus import GENERIC_QUERY_URL, json_error, protheus_auth

from .docs import DOCS
from .models import DEFAULT_LIMIT, MAX_ROWS, BloqueioEstoqueResponse

bp = func.Blueprint()

# Filtros aceitos: parâmetro da query string -> coluna da SC9. O usuário
# informa exatamente um deles.
_FILTROS = {
    "carga": "C9_CARGA",
    "ordem_separacao": "C9_ORDSEP",
    "pedido": "C9_PEDIDO",
}

# C9_CARGA, C9_ORDSEP e C9_PEDIDO são campos numéricos armazenados como
# caracter de 6 posições com zeros à esquerda (ex: '006407'). Aceita-se só
# dígitos (até 6) e o valor é completado com zfill(6) antes da consulta.
# Restringir a dígitos também protege o FromQry (que viaja em header HTTP)
# contra quebra do SQL, já que o valor é interpolado nele.
_FILTRO_RE = re.compile(r"^\d{1,6}$")
_FILTRO_TAMANHO = 6

# FromQry com a consulta de bloqueio de estoque: saldos da SB2 restritos aos
# produtos/armazéns dos itens liberados (SC9) que atendem ao filtro, com os
# saldos por lote (SB8) agregados por produto/armazém via LEFT JOIN — uma
# linha por registro da SB2. A subquery devolve apenas colunas reais do
# dicionário — as colunas derivadas (saldo_disponivel, disponivel_lote) são
# calculadas em Python; os agregados da SB8 são aliased para colunas reais
# da própria SB8 (COUNT(*) usa B8_QTDORI, que não é retornada de verdade)
# porque a genericQuery valida 'fields' contra o dicionário. As colunas do
# índice da SB2 (B2_FILIAL, B2_COD, B2_LOCAL) precisam existir na subquery
# mesmo que não sejam pedidas em 'fields': a genericQuery as referencia
# internamente e retorna 500 se faltarem.
_FROM_QRY = (
    "(SELECT "
    "T0.B2_FILIAL AS B2_FILIAL, "
    "T0.B2_COD AS B2_COD, "
    "T0.B2_LOCAL AS B2_LOCAL, "
    "T0.B2_QATU AS B2_QATU, "
    "T0.B2_RESERVA AS B2_RESERVA, "
    "T0.B2_QEMP AS B2_QEMP, "
    "NVL(T2.QTD_LOTES, 0) AS B8_QTDORI, "
    "NVL(T2.SALDO_LOTE, 0) AS B8_SALDO, "
    "NVL(T2.EMPENHADO_LOTE, 0) AS B8_EMPENHO, "
    "T2.PROXIMA_VALIDADE AS B8_DTVALID, "
    "T0.R_E_C_N_O_ AS R_E_C_N_O_, "
    "T0.R_E_C_D_E_L_ AS R_E_C_D_E_L_, "
    "T0.D_E_L_E_T_ AS D_E_L_E_T_ "
    "FROM SB2010 T0 "
    "INNER JOIN "
    "(SELECT DISTINCT C9_FILIAL, C9_PRODUTO, C9_LOCAL "
    "FROM SC9010 "
    "WHERE {filtro_coluna} = '{filtro_valor}' AND D_E_L_E_T_ = ' ') T1 "
    "ON T0.B2_FILIAL = T1.C9_FILIAL "
    "AND T0.B2_COD = T1.C9_PRODUTO "
    "AND T0.B2_LOCAL = T1.C9_LOCAL "
    "LEFT JOIN "
    "(SELECT "
    "B8_FILIAL, "
    "B8_PRODUTO, "
    "B8_LOCAL, "
    "COUNT(*) AS QTD_LOTES, "
    "SUM(B8_SALDO) AS SALDO_LOTE, "
    "SUM(B8_EMPENHO) AS EMPENHADO_LOTE, "
    "MIN(B8_DTVALID) AS PROXIMA_VALIDADE "
    "FROM SB8010 "
    "WHERE D_E_L_E_T_ = ' ' "
    "GROUP BY B8_FILIAL, B8_PRODUTO, B8_LOCAL) T2 "
    "ON T2.B8_FILIAL = T0.B2_FILIAL "
    "AND T2.B8_PRODUTO = T0.B2_COD "
    "AND T2.B8_LOCAL = T0.B2_LOCAL "
    "WHERE T0.D_E_L_E_T_ = ' ' "
    # Ordena pelo saldo disponível da SB2 (mesma expressão calculada em
    # Python) com R_E_C_N_O_ como desempate: com OFFSET a ordenação precisa
    # ser total, senão linhas empatadas podem trocar de página entre
    # requisições.
    "ORDER BY (T0.B2_QATU - T0.B2_RESERVA - T0.B2_QEMP), T0.R_E_C_N_O_ "
    "OFFSET {offset} ROWS FETCH NEXT {rows} ROWS ONLY"
    ") SB2"
)

# Colunas do CSV: chave do item -> cabeçalho com os nomes do relatório original.
_CSV_COLUMNS = [
    ("filial", "Filial"),
    ("produto", "Produto"),
    ("armazem", "Armazem"),
    ("saldo_atual", "SaldoAtual"),
    ("qtd_reservada", "QtdReservada"),
    ("qtd_empenhada", "QtdEmpenhada"),
    ("saldo_disponivel", "SaldoDisponivel"),
    ("qtd_lotes", "QtdLotes"),
    ("saldo_lote", "SaldoLote"),
    ("empenhado_lote", "EmpenhadoLote"),
    ("disponivel_lote", "DisponivelLote"),
    ("proxima_validade", "ProximaValidade"),
]


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
    # Agregados da SB8 (aliased no FromQry): b8_qtdori carrega o COUNT(*) de
    # lotes; NVL no SQL já garante 0 quando o produto não tem lote.
    saldo_lote = _to_float(row.get("b8_saldo"))
    empenhado_lote = _to_float(row.get("b8_empenho"))
    return {
        "filial": str(row.get("b2_filial", "")),
        "produto": str(row.get("b2_cod", "")).strip(),
        "armazem": str(row.get("b2_local", "")),
        "saldo_atual": saldo_atual,
        "qtd_reservada": qtd_reservada,
        "qtd_empenhada": qtd_empenhada,
        "saldo_disponivel": saldo_atual - qtd_reservada - qtd_empenhada,
        "qtd_lotes": int(_to_float(row.get("b8_qtdori"))),
        "saldo_lote": saldo_lote,
        "empenhado_lote": empenhado_lote,
        "disponivel_lote": saldo_lote - empenhado_lote,
        "proxima_validade": str(row.get("b8_dtvalid") or "").strip() or None,
    }


@openapi(**DOCS)
@bp.route(route="bloqueio-estoque", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
@require_roles("Reports.Read")
def bloqueio_estoque(req: func.HttpRequest) -> func.HttpResponse:

    filtros_informados = {
        nome: req.params.get(nome, "").strip()
        for nome in _FILTROS
        if req.params.get(nome, "").strip()
    }
    if len(filtros_informados) != 1:
        return json_error(
            "Informe exatamente um dos filtros: 'carga', 'ordem_separacao' ou 'pedido'",
            400,
        )
    filtro, valor = next(iter(filtros_informados.items()))
    if not _FILTRO_RE.match(valor):
        return json_error(
            f"'{filtro}' deve conter apenas dígitos (máx. {_FILTRO_TAMANHO}), "
            f"ex: 006407",
            400,
        )
    # Campo caracter de 6 posições no Protheus: completa com zeros à
    # esquerda para casar com o valor armazenado (ex: '6407' -> '006407').
    valor = valor.zfill(_FILTRO_TAMANHO)

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
        "tables": "SB2,SB8",
        "fields": (
            "B2_FILIAL,B2_COD,B2_LOCAL,B2_QATU,B2_RESERVA,B2_QEMP,"
            "B8_QTDORI,B8_SALDO,B8_EMPENHO,B8_DTVALID"
        ),
        # Uma linha a mais que a página para saber se há próxima página.
        "pagesize": str(limit + 1),
        "FromQry": _FROM_QRY.format(
            filtro_coluna=_FILTROS[filtro],
            filtro_valor=valor,
            offset=offset,
            rows=limit + 1,
        ),
        "FilialFilter": "false",
        # Os filtros de deleção já estão embutidos no FromQry; o automático
        # qualificaria D_E_L_E_T_ também no alias SB8, inexistente na subquery.
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
        logging.error("Protheus bloqueioEstoque failed: %s", exc)
        return json_error("Falha ao conectar à API do Protheus", 502)

    if resp.status_code >= 400:
        logging.error(
            "Protheus bloqueioEstoque erro HTTP %s: %.500s", resp.status_code, resp.text
        )
        return json_error(f"Protheus retornou erro HTTP {resp.status_code}.", 502)

    try:
        data = resp.json()
    except ValueError:
        logging.error(
            "Protheus bloqueioEstoque retornou resposta não-JSON (status=%s): %.500s",
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
                "Content-Disposition": 'attachment; filename="bloqueio-estoque.csv"',
                "X-Row-Count": str(len(items)),
                "X-Has-Next": str(has_next).lower(),
            },
        )

    return func.HttpResponse(
        BloqueioEstoqueResponse(
            filtro=filtro,
            valor=valor,
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
