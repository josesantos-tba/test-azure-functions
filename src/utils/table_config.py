"""Sistema de configuração por tabela do Protheus.

Centraliza as regras de governança aplicadas por tabela (filtro obrigatório,
liberação de exportação CSV, colunas obrigatórias/permitidas e limite de linhas).
As regras são carregadas do arquivo ``src/config/table_config.json`` **uma única
vez na importação** — configuração inválida falha no boot, não em runtime.

O arquivo é editado apenas por desenvolvedores (versionado no git). O
``filtro_obrigatorio`` usa o **mesmo formato JSON** do parâmetro ``filters`` do
endpoint ``query-json`` (uma lista de objetos com ``column``/``operator``/
``value``/``value2``/``type``) e pode conter **vários** filtros, combinados com
AND. Como é uma regra de governança, ele é **sempre** aplicado no servidor e
nunca pode ser removido ou sobreposto pelo cliente.
"""

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, field_validator

from src.utils.protheus_filters import build_conditions

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "table_config.json"

# Chave especial no JSON com os valores herdados por todas as tabelas.
_DEFAULT_KEY = "_default"


class TableConfig(BaseModel):
    """Regras de governança de uma tabela."""

    habilitada: bool = True
    csv_export_habilitado: bool = True
    # Lista de filtros no formato do `query-json` (ou None). Ver build_conditions.
    filtro_obrigatorio: list[dict[str, Any]] | None = None
    colunas_obrigatorias: list[str] = []
    colunas_permitidas: list[str] | None = None
    limite_max_linhas: int | None = None

    @field_validator("colunas_obrigatorias", "colunas_permitidas")
    @classmethod
    def _upper(cls, value: list[str] | None) -> list[str] | None:
        """Normaliza nomes de coluna para maiúsculas (comparação case-insensitive)."""
        if value is None:
            return None
        return [c.strip().upper() for c in value]


def _compile_filter(name: str, cfg: TableConfig) -> list[str]:
    """Valida e compila o ``filtro_obrigatorio`` da tabela em condições SQL.

    Levanta ``ValueError`` (falha no boot) quando os filtros são inválidos.
    """
    if not cfg.filtro_obrigatorio:
        return []
    conditions, err = build_conditions(cfg.filtro_obrigatorio)
    if err:
        raise ValueError(
            f"table_config.json: filtro_obrigatorio inválido em '{name}': {err}"
        )
    return conditions or []


def _load() -> tuple[TableConfig, dict[str, TableConfig], list[str], dict[str, list[str]]]:
    """Carrega e valida o JSON.

    Devolve (config_default, config_por_tabela, filtro_default_compilado,
    filtros_compilados_por_tabela).
    """
    raw = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))

    default_overrides = raw.get(_DEFAULT_KEY, {})
    default = TableConfig(**default_overrides)
    default_compiled = _compile_filter(_DEFAULT_KEY, default)

    tables: dict[str, TableConfig] = {}
    compiled: dict[str, list[str]] = {}
    for name, overrides in raw.items():
        if name == _DEFAULT_KEY:
            continue
        # Cada tabela herda do _default e sobrescreve o que declarar.
        merged = {**default.model_dump(), **overrides}
        cfg = TableConfig(**merged)

        # Consistência: colunas obrigatórias precisam estar na whitelist.
        if cfg.colunas_permitidas is not None:
            faltando = set(cfg.colunas_obrigatorias) - set(cfg.colunas_permitidas)
            if faltando:
                raise ValueError(
                    f"table_config.json: tabela '{name}' tem colunas_obrigatorias "
                    f"fora de colunas_permitidas: {sorted(faltando)}"
                )

        key = name.strip().upper()
        tables[key] = cfg
        compiled[key] = _compile_filter(name, cfg)

    return default, tables, default_compiled, compiled


_DEFAULT_CONFIG, _TABLES, _DEFAULT_COMPILED, _COMPILED = _load()


def get_table_config(table: str) -> TableConfig:
    """Devolve a config da tabela (ou a config padrão quando não há entrada)."""
    return _TABLES.get(table.strip().upper(), _DEFAULT_CONFIG)


def has_table_config(table: str) -> bool:
    """Indica se a tabela tem configuração própria (vs. herdar o ``_default``)."""
    return table.strip().upper() in _TABLES


def table_enabled(table: str) -> bool:
    """Indica se a tabela está habilitada em todos os endpoints."""
    return get_table_config(table).habilitada


def csv_enabled(table: str) -> bool:
    """Indica se a exportação CSV está liberada para a tabela."""
    return get_table_config(table).csv_export_habilitado


def mandatory_filter(table: str) -> list[str]:
    """Condições SQL (já compiladas) sempre aplicadas à tabela.

    Pode conter mais de uma condição (uma por filtro obrigatório); todas são
    combinadas com AND pelo chamador. Lista vazia quando não há filtro.
    """
    return _COMPILED.get(table.strip().upper(), _DEFAULT_COMPILED)


def effective_limit(table: str, requested: int, global_max: int) -> int:
    """Limite efetivo de linhas: menor entre o pedido, o da tabela e o global."""
    cfg_limit = get_table_config(table).limite_max_linhas
    cap = min(cfg_limit, global_max) if cfg_limit is not None else global_max
    return min(requested, cap)


def enforce_columns(table: str, requested_fields: str) -> tuple[str | None, str | None]:
    """Aplica whitelist e colunas obrigatórias sobre o ``fields`` pedido.

    Rejeita (erro 400) colunas fora de ``colunas_permitidas`` e garante que as
    ``colunas_obrigatorias`` estejam presentes. Retorna ``(fields, None)`` em
    caso de sucesso ou ``(None, mensagem_de_erro)``.
    """
    cfg = get_table_config(table)
    requested = [c.strip() for c in requested_fields.split(",") if c.strip()]
    present = {c.upper() for c in requested}

    if cfg.colunas_permitidas is not None:
        allowed = set(cfg.colunas_permitidas)
        invalid = [c for c in requested if c.upper() not in allowed]
        if invalid:
            return None, (
                f"Colunas não permitidas para a tabela '{table.upper()}': "
                f"{', '.join(invalid)}"
            )

    final = list(requested)
    for col in cfg.colunas_obrigatorias:
        if col not in present:
            final.append(col)
            present.add(col)

    return ",".join(final), None
