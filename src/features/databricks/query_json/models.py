from typing import Any

from pydantic import BaseModel, Field

# Parâmetros de contrato expostos na documentação e usados pelo handler.
DEFAULT_PAGESIZE = 100
MAX_PAGESIZE = 10000


class QueryResponse(BaseModel):
    tabela: str = Field(description="Tabela consultada (dentro do catalog/schema configurados).")
    page: int = Field(description="Página atual (1-based).")
    pagesize: int = Field(description="Registros por página.")
    has_next: bool = Field(description="Indica se há uma próxima página.")
    next_page: int | None = Field(
        description="Número da próxima página, ou `null` quando não há próxima."
    )
    items: list[dict[str, Any]] = Field(
        description=(
            "Registros retornados. Cada item é um objeto dinâmico cujas chaves "
            "correspondem às colunas pedidas em `fields` e os valores refletem o "
            "tipo da coluna no Databricks."
        )
    )
