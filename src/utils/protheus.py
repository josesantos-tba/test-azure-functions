"""Configuração e utilidades compartilhadas para acesso à API do Protheus.

Centraliza o que antes era duplicado em cada endpoint: a URL base e as
credenciais (lidas do ambiente), a URL da ``genericQuery``, o schema OpenAPI
das respostas de erro e o helper que monta a resposta JSON de erro.
"""

import json
import os

import azure.functions as func

PROTHEUS_BASE_URL = os.environ.get("PROTHEUS_BASE_URL", "")
PROTHEUS_AUTH = (
    os.environ.get("PROTHEUS_USER", ""),
    os.environ.get("PROTHEUS_PASSWORD", ""),
)

# Endpoint único usado por todos os slices para consultar o Protheus.
GENERIC_QUERY_URL = f"{PROTHEUS_BASE_URL}/api/framework/v1/genericQuery"

# Schema OpenAPI compartilhado das respostas de erro ({"error": "..."}).
ERROR_SCHEMA = {
    "type": "object",
    "properties": {"error": {"type": "string", "example": "Mensagem de erro"}},
    "required": ["error"],
}


def protheus_auth() -> tuple[str, str] | None:
    """Credenciais básicas do Protheus, ou ``None`` quando não configuradas.

    Fora do Azure (execução local) as variáveis podem não existir; nesse caso
    a requisição é feita sem autenticação, preservando o comportamento antigo
    (``auth=... if all(...) else None``).
    """
    return PROTHEUS_AUTH if all(PROTHEUS_AUTH) else None


def json_error(message: str, status: int) -> func.HttpResponse:
    """Resposta JSON padronizada de erro (``{"error": message}``)."""
    return func.HttpResponse(
        json.dumps({"error": message}),
        status_code=status,
        mimetype="application/json",
    )
