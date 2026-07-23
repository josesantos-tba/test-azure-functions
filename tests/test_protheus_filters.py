"""Testes dos filtros do Protheus — travam o contrato de operadores e tipos.

Foco: garantir que os operadores sejam os aceitos, que
cada um gere o SQL esperado e que os nomes antigos em português sejam rejeitados.
"""

import json

import pytest

from src.utils import protheus_filters as pf

# Conjunto de operadores que compõe o contrato público. Se alguém adicionar,
# remover ou renomear um operador, este teste falha de propósito.
EXPECTED_OPERATORS = {
    "=", "!=", ">", "<", "<=", ">=",
    "contains", "starts_with", "ends_with",
    "between", "is_blank", "is_not_blank",
}

EXPECTED_TYPES = {"string", "number", "date"}


def _cond(filtro: dict) -> str:
    """Compila um único filtro e devolve a condição SQL (falha se houver erro)."""
    conditions, err = pf.parse_filters(json.dumps([filtro]))
    assert err is None, err
    assert conditions is not None and len(conditions) == 1
    return conditions[0]


def test_operator_set_is_the_english_contract():
    assert pf._VALID_OPERATORS == EXPECTED_OPERATORS
    assert pf._VALID_TYPES == EXPECTED_TYPES


def test_reference_doc_lists_every_operator_and_type():
    for token in EXPECTED_OPERATORS | EXPECTED_TYPES:
        assert f"`{token}`" in pf.FILTERS_REFERENCE_MD


@pytest.mark.parametrize(
    "operator, expected",
    [
        ("=", "C='x'"),
        ("!=", "C!='x'"),
        (">", "C>'x'"),
        ("<", "C<'x'"),
        (">=", "C>='x'"),
        ("<=", "C<='x'"),
    ],
)
def test_comparison_operators(operator, expected):
    assert _cond({"column": "C", "operator": operator, "value": "x"}) == expected


@pytest.mark.parametrize(
    "operator, expected",
    [
        ("contains", "C LIKE '%x%'"),
        ("starts_with", "C LIKE 'x%'"),
        ("ends_with", "C LIKE '%x'"),
    ],
)
def test_like_operators(operator, expected):
    assert _cond({"column": "C", "operator": operator, "value": "x"}) == expected


@pytest.mark.parametrize(
    "operator, expected",
    [
        ("is_blank", "(C IS NULL OR RTRIM(C)='')"),
        ("is_not_blank", "(C IS NOT NULL AND RTRIM(C)<>'')"),
    ],
)
def test_no_value_operators(operator, expected):
    assert _cond({"column": "C", "operator": operator}) == expected


def test_between_with_value2():
    cond = _cond(
        {"column": "N", "operator": "between", "value": 1, "value2": 9, "type": "number"}
    )
    assert cond == "N BETWEEN 1 AND 9"


def test_between_accepts_value_as_list():
    cond = _cond(
        {"column": "N", "operator": "between", "value": [1, 9], "type": "number"}
    )
    assert cond == "N BETWEEN 1 AND 9"


def test_type_number_is_not_quoted():
    assert _cond({"column": "N", "operator": ">=", "value": 1000, "type": "number"}) == "N>=1000"


def test_type_date_is_converted_to_protheus_format():
    assert _cond({"column": "D", "operator": "=", "value": "2025-01-15", "type": "date"}) == "D='20250115'"


def test_string_value_is_escaped():
    # Aspas simples são duplicadas para não quebrar/injetar a cláusula.
    assert _cond({"column": "C", "operator": "=", "value": "O'Brien"}) == "C='O''Brien'"


@pytest.mark.parametrize(
    "old_operator",
    ["contem", "comeca com", "termina em", "entre", "em branco", "nao em branco"],
)
def test_old_portuguese_operators_are_rejected(old_operator):
    conditions, err = pf.parse_filters(
        json.dumps([{"column": "C", "operator": old_operator, "value": "x"}])
    )
    assert conditions is None
    assert err is not None and "Operador inválido" in err


def test_multiple_filters_are_returned_in_order():
    conditions, err = pf.parse_filters(
        json.dumps(
            [
                {"column": "A", "operator": "=", "value": "1"},
                {"column": "B", "operator": "contains", "value": "z"},
            ]
        )
    )
    assert err is None
    assert conditions == ["A='1'", "B LIKE '%z%'"]


def test_invalid_column_is_rejected():
    conditions, err = pf.parse_filters(
        json.dumps([{"column": "C; DROP", "operator": "=", "value": "x"}])
    )
    assert conditions is None
    assert err is not None
