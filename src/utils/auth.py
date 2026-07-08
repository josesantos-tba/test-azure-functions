"""Autorização por *app role* (Microsoft Entra ID) para as Azure Functions.

Quando o Function App usa **App Service Authentication (EasyAuth)** com Entra ID,
a plataforma valida o token e injeta o usuário autenticado no header
``X-MS-CLIENT-PRINCIPAL`` (JSON codificado em base64). Os *app roles* atribuídos
ao usuário/aplicação chegam como claims de tipo ``roles``.

O EasyAuth apenas autentica — não filtra por role. Este módulo lê esse header e
restringe o acesso com base nas roles exigidas via o decorator ``require_roles``.
"""

import base64
import binascii
import functools
import json
import logging
import os
from collections.abc import Callable

import azure.functions as func

_CLIENT_PRINCIPAL_HEADER = "X-MS-CLIENT-PRINCIPAL"

# Tipos de claim que representam app roles no principal do EasyAuth.
_ROLE_CLAIM_TYPES = {
    "roles",
    "http://schemas.microsoft.com/ws/2008/06/identity/claims/role",
}


def _running_in_azure() -> bool:
    """Indica se o código está executando no Azure.

    A plataforma (App Service/Functions) sempre define ``WEBSITE_INSTANCE_ID``;
    em execução local (``func start``) essa variável não existe. Fora do Azure
    não há EasyAuth para injetar o principal, então a autorização é ignorada.
    """
    return bool(os.environ.get("WEBSITE_INSTANCE_ID"))


def _json_error(message: str, status: int) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps({"error": message}),
        status_code=status,
        mimetype="application/json",
    )


def get_principal_roles(req: func.HttpRequest) -> set[str] | None:
    """Extrai as app roles do header ``X-MS-CLIENT-PRINCIPAL``.

    Retorna o conjunto de roles (possivelmente vazio) quando há um principal
    autenticado, ou ``None`` quando o header está ausente (não autenticado).
    """
    encoded = req.headers.get(_CLIENT_PRINCIPAL_HEADER)
    if not encoded:
        return None
    try:
        decoded = base64.b64decode(encoded).decode("utf-8")
        principal = json.loads(decoded)
    except (binascii.Error, ValueError) as exc:
        logging.warning("X-MS-CLIENT-PRINCIPAL inválido: %s", exc)
        return None

    # O tipo do claim de role pode variar conforme a config do Entra ID.
    role_type = principal.get("role_typ")
    valid_types = _ROLE_CLAIM_TYPES | ({role_type} if role_type else set())

    roles: set[str] = set()
    for claim in principal.get("claims", []):
        if claim.get("typ") in valid_types and claim.get("val"):
            roles.add(claim["val"])
    return roles


def require_roles(
    *required_roles: str,
) -> Callable[[Callable[[func.HttpRequest], func.HttpResponse]],
              Callable[[func.HttpRequest], func.HttpResponse]]:
    """Exige ao menos uma das ``required_roles`` no principal autenticado.

    - Execução local (fora do Azure) → autorização ignorada.
    - Header ausente (não autenticado) → HTTP 401.
    - Autenticado, mas sem nenhuma das roles exigidas → HTTP 403.

    Deve ser o decorator **mais interno** (logo acima de ``def``), para que o
    ``@bp.route`` registre o wrapper como handler da função.
    """

    def decorator(
        handler: Callable[[func.HttpRequest], func.HttpResponse],
    ) -> Callable[[func.HttpRequest], func.HttpResponse]:
        @functools.wraps(handler)
        def wrapper(req: func.HttpRequest) -> func.HttpResponse:
            if not _running_in_azure():
                logging.debug("Autorização ignorada: execução local (fora do Azure).")
                return handler(req)
            roles = get_principal_roles(req)
            if roles is None:
                return _json_error("Requisição não autenticada.", 401)
            if not roles.intersection(required_roles):
                logging.info(
                    "Acesso negado: roles do usuário %s não incluem nenhuma de %s",
                    sorted(roles),
                    list(required_roles),
                )
                return _json_error(
                    "Acesso negado: você não tem a permissão necessária "
                    "para acessar este recurso.",
                    403,
                )
            return handler(req)

        return wrapper

    return decorator