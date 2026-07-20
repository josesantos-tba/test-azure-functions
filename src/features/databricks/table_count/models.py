from pydantic import BaseModel, Field


class TableCountResponse(BaseModel):
    tabela: str = Field(description="Tabela contada (dentro do catalog/schema configurados).")
    count: int = Field(
        description=(
            "Quantidade de registros na tabela que atendem aos filtros informados "
            "(todos, se nenhum filtro)."
        )
    )
