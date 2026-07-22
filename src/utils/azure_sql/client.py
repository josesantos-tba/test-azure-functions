"""Configuração e utilidades compartilhadas para acesso ao Azure SQL.

Centraliza a conexão com um banco **Azure SQL Database** (via ``pyodbc``):
credenciais lidas do ambiente, um cursor gerenciado por *context manager*, um
helper que executa consultas e devolve ``list[dict]``, além do schema OpenAPI e
do helper de resposta de erro compartilhados pelos slices que usam Azure SQL.

Variáveis de ambiente esperadas:
    AZURE_SQL_SERVER    ex.: meu-servidor.database.windows.net
    AZURE_SQL_DATABASE  nome do banco
    AZURE_SQL_USERNAME  usuário SQL
    AZURE_SQL_PASSWORD  senha
    AZURE_SQL_SCHEMA    schema padrão das tabelas (default: totvs)
    AZURE_SQL_DRIVER    driver ODBC (default: ODBC Driver 18 for SQL Server)
"""

import json
import os
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

import azure.functions as func

AZURE_SQL_SERVER = os.environ.get("AZURE_SQL_SERVER", "")
AZURE_SQL_DATABASE = os.environ.get("AZURE_SQL_DATABASE", "")
AZURE_SQL_USERNAME = os.environ.get("AZURE_SQL_USERNAME", "")
AZURE_SQL_PASSWORD = os.environ.get("AZURE_SQL_PASSWORD", "")
AZURE_SQL_SCHEMA = os.environ.get("AZURE_SQL_SCHEMA", "totvs")
AZURE_SQL_DRIVER = os.environ.get("AZURE_SQL_DRIVER", "ODBC Driver 18 for SQL Server")

# Schema OpenAPI compartilhado das respostas de erro ({"error": "..."}).
ERROR_SCHEMA = {
    "type": "object",
    "properties": {"error": {"type": "string", "example": "Mensagem de erro"}},
    "required": ["error"],
}


def azure_sql_config_ok() -> bool:
    """Indica se as credenciais mínimas do Azure SQL estão configuradas."""
    return all(
        (
            AZURE_SQL_SERVER,
            AZURE_SQL_DATABASE,
            AZURE_SQL_USERNAME,
            AZURE_SQL_PASSWORD,
        )
    )


def _connection_string() -> str:
    """Monta a connection string ODBC a partir das variáveis de ambiente."""
    return (
        f"DRIVER={{{AZURE_SQL_DRIVER}}};"
        f"SERVER=tcp:{AZURE_SQL_SERVER},1433;"
        f"DATABASE={AZURE_SQL_DATABASE};"
        f"UID={AZURE_SQL_USERNAME};"
        f"PWD={AZURE_SQL_PASSWORD};"
        "Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30;"
    )


@contextmanager
def azure_sql_cursor() -> Iterator[Any]:
    """Abre uma conexão com o Azure SQL e entrega um cursor gerenciado.

    A importação de ``pyodbc`` é *lazy* para que o módulo (e o registro das
    functions no ``app.py``) importe mesmo sem o pacote instalado localmente.
    """
    import pyodbc  # import lazy: só é necessário em runtime

    connection = pyodbc.connect(_connection_string())
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
    with azure_sql_cursor() as cursor:
        cursor.execute(query, parameters or [])
        columns = [desc[0] for desc in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def json_error(message: str, status: int) -> func.HttpResponse:
    """Resposta JSON padronizada de erro ({"error": message})."""
    return func.HttpResponse(
        json.dumps({"error": message}),
        status_code=status,
        mimetype="application/json",
    )
