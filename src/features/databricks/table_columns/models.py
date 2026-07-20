from pydantic import BaseModel, ConfigDict, Field


class DatabricksColumn(BaseModel):
    nome: str
    tipo: str
    posicao: int
    nullable: bool
    comentario: str | None = None


class DatabricksColumnsResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    catalogo: str
    schema_: str = Field(alias="schema")
    tabela: str
    colunas: list[DatabricksColumn]
