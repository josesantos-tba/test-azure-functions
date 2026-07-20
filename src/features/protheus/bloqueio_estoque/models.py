from typing import Any

from pydantic import BaseModel, Field

# Linhas por página (padrão e máximo).
DEFAULT_LIMIT = 100
MAX_ROWS = 10000


class BloqueioEstoqueResponse(BaseModel):
    filtro: str = Field(
        description="Filtro usado na consulta: `carga`, `ordem_separacao` ou `pedido`."
    )
    valor: str = Field(
        description=(
            "Valor do filtro usado na consulta, já normalizado com zeros à "
            "esquerda para 6 posições (ex: `006407`)."
        )
    )
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
            "Saldos de estoque dos produtos/armazéns dos itens liberados que "
            "atendem ao filtro, ordenados por saldo disponível crescente — uma "
            "linha por produto/armazém, com os lotes (SB8) agregados. Cada "
            "item tem as chaves: `filial`, `produto`, `armazem`, "
            "`saldo_atual`, `qtd_reservada`, `qtd_empenhada`, "
            "`saldo_disponivel` (`saldo_atual - qtd_reservada - "
            "qtd_empenhada`), `qtd_lotes`, `saldo_lote` (soma dos saldos dos "
            "lotes), `empenhado_lote` (soma dos empenhos), `disponivel_lote` "
            "(`saldo_lote - empenhado_lote`) e `proxima_validade` (menor "
            "`B8_DTVALID`, formato `AAAAMMDD`; `null` quando o produto não "
            "controla lote)."
        )
    )
