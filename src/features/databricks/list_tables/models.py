from pydantic import BaseModel, ConfigDict, Field


class DatabricksTable(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    catalogo: str
    schema_: str = Field(alias="schema")
    nome: str
    display_name: str


class DatabricksTablesResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    catalogo: str
    schema_: str = Field(alias="schema")
    tabelas: list[DatabricksTable]
