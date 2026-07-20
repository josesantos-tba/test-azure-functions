"""Handler do endpoint ``GET /query-json`` — consulta paginada em JSON."""

import logging
import re
from typing import Any

import azure.functions as func
import requests

from azure_functions_openapi import openapi

from src.utils.auth import require_roles
from src.utils.protheus import GENERIC_QUERY_URL, json_error, protheus_auth
from src.utils.protheus_filters import parse_filters

from .docs import DOCS
from .models import DEFAULT_PAGESIZE, MAX_PAGESIZE, TABLE_SUFFIX, QueryResponse

bp = func.Blueprint()

# Alias de tabela válido (evita injeção via nome de tabela no FromQry).
_TABLE_RE = re.compile(r"^[A-Za-z0-9_]+$")

# FromQry único (página + cursores em uma só chamada à genericQuery), com o
# WHERE (cursor de recno, deleção e filtros dinâmicos) embutido na subquery,
# sem usar o parâmetro 'where' do Protheus. Ordena por R_E_C_N_O_ DESC para
# os registros mais recentes virem primeiro e busca pagesize+1 linhas para
# saber se há próxima página.
#
# Como o Protheus não retorna a coluna R_E_C_N_O_ nos itens, cada linha
# carrega o próprio R_E_C_N_O_ numa coluna numérica "emprestada" de outra
# tabela padrão (ex: A10_PRAZO — numérica, sem truncamento) e o escalar do
# previous_recno em outra (ex: E5_VALOR). A genericQuery aceita múltiplos
# aliases em 'tables' separados por vírgula, o que permite pedir em 'fields'
# colunas dessas tabelas junto com as da tabela consultada.
_FROM_QRY_TEMPLATE = (
    "(SELECT T.*, T.R_E_C_N_O_ AS {recno_col}, {prev_expr} AS {prev_col} "
    "FROM {table} T WHERE {where} "
    "ORDER BY T.R_E_C_N_O_ DESC FETCH NEXT {rows} ROWS ONLY) {alias}"
)

# Escalar do previous_recno: o pagesize-ésimo R_E_C_N_O_ acima do cursor.
# Não retorna linha (vira NULL na coluna) quando há menos de pagesize
# registros acima — nesse caso a página anterior é a primeira (cursor 0).
_PREV_EXPR_TEMPLATE = (
    "(SELECT MAX(R_E_C_N_O_) FROM ("
    "SELECT R_E_C_N_O_ FROM {table} WHERE {where_above} "
    "ORDER BY R_E_C_N_O_ ASC FETCH NEXT {pagesize} ROWS ONLY) "
    "HAVING COUNT(*) >= {pagesize})"
)

# Colunas numéricas de tabelas padrão do Protheus usadas como "carona" para
# o R_E_C_N_O_ de cada linha e para o escalar do previous_recno. São usadas
# as duas primeiras cuja tabela difere da consultada, para não colidir com
# as colunas reais quando a própria A10/SE5 for consultada.
_SENTINEL_COLUMNS = [("A10", "A10_PRAZO"), ("SE5", "E5_VALOR"), ("SE2", "E2_VALOR")]


def _parse_int(value: str, default: int) -> int | None:
    """Retorna o inteiro contido em ``value`` ou ``default`` se vazio; ``None`` se inválido."""
    value = value.strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return None


def _to_int(value: Any) -> int | None:
    """Converte um valor vindo do Protheus (número, string ou vazio) em int, ou ``None``."""
    try:
        text = str(value).strip()
        if not text:
            return None
        return int(float(text))
    except (TypeError, ValueError):
        return None


def _protheus_query(
    headers: dict[str, str],
) -> tuple[dict[str, Any] | None, func.HttpResponse | None]:
    """Chama a genericQuery com os ``headers`` informados.

    Retorna ``(json, None)`` em caso de sucesso ou ``(None, resposta_de_erro)``.
    """
    try:
        resp = requests.get(
            GENERIC_QUERY_URL,
            auth=protheus_auth(),
            timeout=60 * 10,
            headers=headers,
        )
    except requests.RequestException as exc:
        logging.error("Protheus queryJson failed: %s", exc)
        return None, json_error("Falha ao conectar à API do Protheus", 502)

    if resp.status_code >= 400:
        # Causa comum de 500 no Protheus: coluna inexistente em 'filters'
        # (em 'fields' o Protheus apenas ignora a coluna).
        logging.error(
            "Protheus queryJson erro HTTP %s: %.500s", resp.status_code, resp.text
        )
        return None, json_error(
            f"Protheus retornou erro HTTP {resp.status_code}. "
            "Verifique se as colunas usadas em 'filters' existem na tabela.",
            502,
        )

    try:
        return resp.json(), None
    except ValueError:
        logging.error(
            "Protheus queryJson retornou resposta não-JSON (status=%s): %.500s",
            resp.status_code,
            resp.text,
        )
        return None, json_error(
            "Resposta inválida do Protheus (não-JSON). Verifique os campos e o tamanho da consulta.",
            502,
        )


@openapi(**DOCS)
@bp.route(route="query-json", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
@require_roles("Tables.Read")
def query_json(req: func.HttpRequest) -> func.HttpResponse:

    table = req.params.get("table", "").strip().upper()
    fields = req.params.get("fields", "").strip()
    filters_raw = req.params.get("filters", "").strip()

    missing = [p for p, v in [("table", table), ("fields", fields)] if not v]
    if missing:
        return json_error(
            f"Parâmetros obrigatórios ausentes: {', '.join(missing)}", 400
        )

    if not _TABLE_RE.match(table):
        return json_error(f"Alias de tabela inválido: '{table}'", 400)

    recno = _parse_int(req.params.get("recno", ""), 0)
    pagesize = _parse_int(req.params.get("pagesize", ""), DEFAULT_PAGESIZE)
    if recno is None or recno < 0:
        return json_error("'recno' deve ser um inteiro >= 0", 400)
    if pagesize is None or pagesize < 1:
        return json_error("'pagesize' deve ser um inteiro >= 1", 400)

    pagesize = min(pagesize, MAX_PAGESIZE)

    base_conditions = ["D_E_L_E_T_ <> '*'"]

    if filters_raw:
        conditions, err = parse_filters(filters_raw)
        if err:
            return json_error(err, 400)
        base_conditions.extend(conditions)

    physical_table = f"{table}{TABLE_SUFFIX}"

    # Página atual: registros abaixo do cursor (ou do topo, na primeira página).
    page_bound = f"R_E_C_N_O_ < {recno}" if recno else "R_E_C_N_O_ > 0"
    where_page = " AND ".join([page_bound, *base_conditions])
    # Janela acima do cursor: usada para calcular o previous_recno.
    where_above = " AND ".join([f"R_E_C_N_O_ > {recno}", *base_conditions])

    # Colunas "emprestadas" que carregam o recno de cada linha e o escalar do
    # previous_recno (só calculado quando não é a primeira página).
    (recno_table, recno_col), (prev_table, prev_col) = [
        pair for pair in _SENTINEL_COLUMNS if pair[0] != table
    ][:2]
    prev_expr = (
        _PREV_EXPR_TEMPLATE.format(
            table=physical_table, where_above=where_above, pagesize=pagesize
        )
        if recno
        else "0"
    )

    data, error_response = _protheus_query(
        {
            "tables": f"{table},{recno_table},{prev_table}",
            "fields": f"{fields},{recno_col},{prev_col}",
            # Uma linha a mais que a página para saber se há próxima página.
            "pagesize": str(pagesize + 1),
            "FromQry": _FROM_QRY_TEMPLATE.format(
                table=physical_table,
                where=where_page,
                rows=pagesize + 1,
                alias=table,
                recno_col=recno_col,
                prev_col=prev_col,
                prev_expr=prev_expr,
            ),
            "FilialFilter": "false",
            # O filtro automático de deleção qualificaria D_E_L_E_T_ também nos
            # aliases das tabelas emprestadas (inexistentes no FromQry); a
            # condição já está embutida em where_page.
            "DeletedFilter": "false",
        }
    )
    if error_response is not None:
        return error_response

    recno_key, prev_key = recno_col.lower(), prev_col.lower()
    rows: list[dict[str, Any]] = data.get("items", [])
    rows.sort(key=lambda row: _to_int(row.get(recno_key)) or 0, reverse=True)
    page_rows = rows[:pagesize]

    # Há próxima página se a janela pagesize+1 veio cheia; o cursor é o menor
    # recno da página atual (a próxima página são os registros abaixo dele).
    next_recno: int | None = None
    if len(rows) > pagesize and page_rows:
        next_recno = _to_int(page_rows[-1].get(recno_key))

    if recno == 0:
        # Primeira página: não há anterior.
        previous_recno: int | None = None
    else:
        # O escalar (repetido em todas as linhas) é o pagesize-ésimo recno
        # acima do cursor; NULL quando a anterior é a primeira página.
        previous_recno = (_to_int(page_rows[0].get(prev_key)) if page_rows else None) or 0

    items = [
        {k: v for k, v in row.items() if k not in (recno_key, prev_key)}
        for row in page_rows
    ]

    return func.HttpResponse(
        QueryResponse(
            pagesize=pagesize,
            previous_recno=previous_recno,
            next_recno=next_recno,
            items=items,
        ).model_dump_json(),
        status_code=200,
        mimetype="application/json",
    )
