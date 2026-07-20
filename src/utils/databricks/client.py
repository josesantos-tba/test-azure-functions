"""Configuração e utilidades compartilhadas para acesso ao Databricks.

Centraliza a conexão com um **SQL Warehouse** do Databricks (via
``databricks-sql-connector``): credenciais lidas do ambiente, um cursor
gerenciado por *context manager*, um helper que executa consultas e devolve
``list[dict]``, além do schema OpenAPI e do helper de resposta de erro
compartilhados pelos slices de Databricks.

Variáveis de ambiente esperadas:
    DATABRICKS_SERVER_HOSTNAME  ex.: adb-1234567890.12.azuredatabricks.net
    DATABRICKS_HTTP_PATH        ex.: /sql/1.0/warehouses/abc123def456
    DATABRICKS_TOKEN            Personal Access Token (dapiXXXX...)
    DATABRICKS_CATALOG          catálogo padrão (default: hive_metastore)
    DATABRICKS_SCHEMA           schema padrão (default: default)
"""

import json
import os
import re
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

import azure.functions as func

DATABRICKS_SERVER_HOSTNAME = os.environ.get("DATABRICKS_SERVER_HOSTNAME", "")
DATABRICKS_HTTP_PATH = os.environ.get("DATABRICKS_HTTP_PATH", "")
DATABRICKS_TOKEN = os.environ.get("DATABRICKS_TOKEN", "")
DATABRICKS_CATALOG = os.environ.get("DATABRICKS_CATALOG", "hive_metastore")
DATABRICKS_SCHEMA = os.environ.get("DATABRICKS_SCHEMA", "default")

# Schema OpenAPI compartilhado das respostas de erro ({"error": "..."}).
ERROR_SCHEMA = {
    "type": "object",
    "properties": {"error": {"type": "string", "example": "Mensagem de erro"}},
    "required": ["error"],
}

# Identificador SQL válido (catálogo/schema/tabela). Como catálogo e schema são
# interpolados no ``FROM`` (não podem ser bind parameters), validamos antes de
# usar para evitar injeção de SQL.
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def databricks_config_ok() -> bool:
    """Indica se as credenciais mínimas do Databricks estão configuradas."""
    return all(
        (DATABRICKS_SERVER_HOSTNAME, DATABRICKS_HTTP_PATH, DATABRICKS_TOKEN)
    )


def is_valid_identifier(name: str) -> bool:
    """Valida um identificador SQL simples (catálogo, schema ou tabela)."""
    return bool(_IDENTIFIER_RE.match(name))


@contextmanager
def databricks_cursor() -> Iterator[Any]:
    """Abre uma conexão com o SQL Warehouse e entrega um cursor gerenciado.

    A importação de ``databricks.sql`` é *lazy* para que o módulo (e o registro
    das functions no ``app.py``) importe mesmo sem o pacote instalado localmente.
    """
    from databricks import sql  # import lazy: só é necessário em runtime

    connection = sql.connect(
        server_hostname=DATABRICKS_SERVER_HOSTNAME,
        http_path=DATABRICKS_HTTP_PATH,
        access_token=DATABRICKS_TOKEN,
    )
    try:
        cursor = connection.cursor()
        try:
            yield cursor
        finally:
            cursor.close()
    finally:
        connection.close()


def fetch_all(
    query: str, parameters: Sequence[Any] | None = None
) -> list[dict[str, Any]]:
    """Executa ``query`` e devolve as linhas como ``list[dict]`` (coluna → valor)."""
    with databricks_cursor() as cursor:
        cursor.execute(query, parameters or [])
        columns = [desc[0] for desc in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def json_error(message: str, status: int) -> func.HttpResponse:
    """Resposta JSON padronizada de erro (``{"error": message}``)."""
    return func.HttpResponse(
        json.dumps({"error": message}),
        status_code=status,
        mimetype="application/json",
    )
