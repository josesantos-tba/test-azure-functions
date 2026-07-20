from pydantic import BaseModel, Field

# Sufixo padrão que converte o alias (ex: CTK) no nome físico (ex: CTK010).
TABLE_SUFFIX = "010"


class TableCountResponse(BaseModel):
    table: str = Field(description="Alias da tabela contada (ex: CTK).")
    count: int = Field(
        description=(
            "Quantidade de registros não deletados na tabela que atendem aos "
            "filtros informados (todos, se nenhum filtro)."
        )
    )
