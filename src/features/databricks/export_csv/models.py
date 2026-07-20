"""Contrato do endpoint ``GET /databricks/export-csv``.

Este slice não tem modelo Pydantic de resposta (a saída é um arquivo CSV);
o módulo mantém apenas as constantes de contrato compartilhadas entre a
documentação e o handler.
"""

# Máximo de linhas exportadas por requisição.
MAX_ROWS = 5000
