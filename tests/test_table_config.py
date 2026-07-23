"""Testes do caminho config → filtro do `table_config`.

Garante que o ``filtro_obrigatorio`` (formato do `query-json`) seja compilado em
SQL usando os operadores em inglês e que config inválida falhe no carregamento.
"""

import pytest

from src.utils import table_config as tc


def test_compile_filter_uses_english_operators():
    cfg = tc.TableConfig(
        filtro_obrigatorio=[
            {"column": "E5_TIPO", "operator": "=", "value": "VL", "type": "string"},
            {"column": "E5_VALOR", "operator": ">", "value": 0, "type": "number"},
        ]
    )
    assert tc._compile_filter("SE5", cfg) == ["E5_TIPO='VL'", "E5_VALOR>0"]


def test_compile_filter_supports_multiple_filters():
    cfg = tc.TableConfig(
        filtro_obrigatorio=[
            {"column": "A", "operator": "starts_with", "value": "01"},
            {"column": "B", "operator": "between", "value": [1, 9], "type": "number"},
            {"column": "C", "operator": "is_not_blank"},
        ]
    )
    assert tc._compile_filter("T", cfg) == [
        "A LIKE '01%'",
        "B BETWEEN 1 AND 9",
        "(C IS NOT NULL AND RTRIM(C)<>'')",
    ]


def test_compile_filter_empty_when_no_filter():
    assert tc._compile_filter("T", tc.TableConfig()) == []


def test_compile_filter_rejects_portuguese_operator():
    cfg = tc.TableConfig(
        filtro_obrigatorio=[{"column": "C", "operator": "contem", "value": "x"}]
    )
    with pytest.raises(ValueError):
        tc._compile_filter("SE5", cfg)


def test_mandatory_filter_returns_list_and_falls_back_to_default():
    assert isinstance(tc.mandatory_filter("SE5"), list)
    assert tc.mandatory_filter("__tabela_inexistente__") == tc._DEFAULT_COMPILED


def test_real_config_loads_and_compiles():
    # A config real (table_config.json) deve ter carregado e compilado sem erro.
    assert isinstance(tc._COMPILED, dict)
    for conditions in tc._COMPILED.values():
        assert isinstance(conditions, list)
        assert all(isinstance(c, str) for c in conditions)
