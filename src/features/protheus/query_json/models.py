from typing import Any

from pydantic import BaseModel, Field

# Parâmetros de contrato expostos na documentação e usados pelo handler.
DEFAULT_PAGESIZE = 100
MAX_PAGESIZE = 10000

# Sufixo padrão que converte o alias (ex: CTK) no nome físico (ex: CTK010).
TABLE_SUFFIX = "010"


class QueryResponse(BaseModel):
    pagesize: int = Field(description="Registros por página.")
    previous_recno: int | None = Field(
        description=(
            "Valor de `recno` para buscar a página anterior (registros mais recentes). "
            "`0` indica que a anterior é a primeira página; `null` indica que esta já "
            "é a primeira página."
        )
    )
    next_recno: int | None = Field(
        description=(
            "Valor de `recno` para buscar a próxima página (registros mais antigos). "
            "`null` quando não há próxima página."
        )
    )
    items: list[dict[str, Any]] = Field(
        description=(
            "Registros retornados, ordenados do mais recente para o mais antigo "
            "(R_E_C_N_O_ decrescente). Cada item é um objeto dinâmico cujas chaves "
            "correspondem às colunas pedidas em `fields` (em minúsculas) e os valores "
            "podem ser de qualquer tipo (string, número, etc.) conforme o campo no Protheus."
        )
    )
