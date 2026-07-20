from pydantic import BaseModel


class TableColumn(BaseModel):
    campo: str
    titulo: str
    tipo: str
    tamanho: int


class TableColumnsResponse(BaseModel):
    tabela: str
    colunas: list[TableColumn]
