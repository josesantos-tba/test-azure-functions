"""Testes dos filtros do Databricks — travam o contrato de operadores e tipos.

Diferente do Protheus, aqui o SQL é **parametrizado**: as condições usam ``?`` e
os valores voltam separados (bind parameters). Os operadores aceitos, porém, são
os mesmos (mesmo contrato).
"""

import json

import pytest

from src.utils.databricks import filters as df

EXPECTED_OPERATORS = {
    "=", "!=", ">", "<", "<=", ">=",
    "contains", "starts_with", "ends_with",
    "between", "is_blank", "is_not_blank",
}

EXPECTED_TYPES = {"string", "number", "date"}


def _one(filtro: dict) -> tuple[str, list]:
    """Compila um único filtro e devolve (condição, params)."""
    conditions, params, err = df.parse_filters(json.dumps([filtro]))
    assert err is None, err
    assert conditions is not None and len(conditions) == 1
    return conditions[0], params


def test_operator_set_matches_protheus_contract():
    # Os dois módulos precisam aceitar exatamente os mesmos operadores/tipos.
    assert df._VALID_OPERATORS == EXPECTED_OPERATORS
    assert df._VALID_TYPES == EXPECTED_TYPES


@pytest.mark.parametrize("operator", ["=", "!=", ">", "<", "<=", ">="])
def test_comparison_operators_are_parameterized(operator):
    cond, params = _one({"column": "C", "operator": operator, "value": "x"})
    assert cond == f"C {operator} ?"
    assert params == ["x"]


@pytest.mark.parametrize(
    "operator, pattern",
    [
        ("contains", "%x%"),
        ("starts_with", "x%"),
        ("ends_with", "%x"),
    ],
)
def test_like_operators(operator, pattern):
    cond, params = _one({"column": "C", "operator": operator, "value": "x"})
    assert cond == "C LIKE ?"
    assert params == [pattern]


@pytest.mark.parametrize(
    "operator, expected_sql",
    [
        ("is_blank", "(C IS NULL OR trim(cast(C AS string)) = '')"),
        ("is_not_blank", "(C IS NOT NULL AND trim(cast(C AS string)) <> '')"),
    ],
)
def test_no_value_operators(operator, expected_sql):
    cond, params = _one({"column": "C", "operator": operator})
    assert cond == expected_sql
    assert params == []


def test_between_with_value2():
    cond, params = _one(
        {"column": "N", "operator": "between", "value": 1, "value2": 9, "type": "number"}
    )
    assert cond == "N BETWEEN ? AND ?"
    assert params == [1, 9]


def test_between_accepts_value_as_list():
    cond, params = _one(
        {"column": "N", "operator": "between", "value": [1, 9], "type": "number"}
    )
    assert cond == "N BETWEEN ? AND ?"
    assert params == [1, 9]


def test_number_keeps_int_and_float():
    _, p_int = _one({"column": "N", "operator": "=", "value": 1000, "type": "number"})
    _, p_float = _one({"column": "N", "operator": "=", "value": 10.5, "type": "number"})
    assert p_int == [1000] and isinstance(p_int[0], int)
    assert p_float == [10.5] and isinstance(p_float[0], float)


def test_date_is_validated_and_passed_as_string():
    cond, params = _one({"column": "D", "operator": "=", "value": "2025-01-15", "type": "date"})
    assert cond == "D = ?"
    assert params == ["2025-01-15"]


@pytest.mark.parametrize(
    "old_operator",
    ["contem", "comeca com", "termina em", "entre", "em branco", "nao em branco"],
)
def test_old_portuguese_operators_are_rejected(old_operator):
    conditions, params, err = df.parse_filters(
        json.dumps([{"column": "C", "operator": old_operator, "value": "x"}])
    )
    assert conditions is None
    assert params == []
    assert err is not None and "Operador inválido" in err


def test_multiple_filters_collect_params_in_order():
    conditions, params, err = df.parse_filters(
        json.dumps(
            [
                {"column": "A", "operator": "=", "value": "1"},
                {"column": "B", "operator": "between", "value": [2, 3], "type": "number"},
            ]
        )
    )
    assert err is None
    assert conditions == ["A = ?", "B BETWEEN ? AND ?"]
    assert params == ["1", 2, 3]
