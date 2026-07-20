from typing import Any

from pydantic import BaseModel, Field

# Linhas por página (padrão e máximo).
DEFAULT_LIMIT = 100
MAX_ROWS = 10000


class SaldoEstoqueResponse(BaseModel):
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
            "Saldos de estoque ordenados por filial, código e armazém. Cada item tem as "
            "chaves: `filial`, `tipo` (Produto Acabado, Embalagem, Matéria-prima "
            "ou Produto em Processo), `codigo`, `descricao`, `armazem`, "
            "`qtd_empenhada`, `saldo_disponivel`, `saldo_atual` e `tba_arm` "
            "(filial + '-' + armazém com 2 dígitos)."
        )
    )
