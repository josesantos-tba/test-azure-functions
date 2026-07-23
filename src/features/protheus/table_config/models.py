from typing import Any

from pydantic import BaseModel, Field


class TableConfigResponse(BaseModel):
    tabela: str = Field(description="Alias da tabela consultada.")
    possui_configuracao_especifica: bool = Field(
        description=(
            "`true` quando a tabela tem uma entrada própria no arquivo de "
            "configuração; `false` quando herda integralmente o `_default`."
        )
    )
    habilitada: bool = Field(
        description="Se a tabela está habilitada nos endpoints de consulta."
    )
    csv_export_habilitado: bool = Field(
        description="Se a exportação CSV está liberada para a tabela."
    )
    filtro_obrigatorio: list[dict[str, Any]] | None = Field(
        description=(
            "Filtros sempre aplicados à tabela, no mesmo formato do parâmetro "
            "`filters` do endpoint `query-json` (lista de objetos com `column`, "
            "`operator`, `value`, `value2` e `type`). `null` quando não há filtro; "
            "pode conter vários filtros, combinados com AND."
        )
    )
    colunas_obrigatorias: list[str] = Field(
        description="Colunas sempre incluídas em `fields` nas consultas."
    )
    colunas_permitidas: list[str] | None = Field(
        description="Whitelist de colunas consultáveis (`null` = todas)."
    )
    limite_max_linhas: int | None = Field(
        description="Teto de linhas próprio da tabela (`null` = usa o limite global)."
    )
