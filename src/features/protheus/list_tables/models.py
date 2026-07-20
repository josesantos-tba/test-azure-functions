from pydantic import BaseModel


class ProtheusTable(BaseModel):
    chave: str
    nome: str
    modo: str
    modulo: int
    pyme: str


class TablesResponse(BaseModel):
    tabelas: list[ProtheusTable]
