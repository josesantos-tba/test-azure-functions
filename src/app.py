import logging
import os
import pathlib
import azure.functions as func
from pydantic import BaseModel
from azure_functions_openapi import ( get_openapi_json, openapi, render_swagger_ui )
from src.blueprints.protheus_get_table_columns import bp as protheus_get_table_columns_bp
from src.blueprints.protheus_generic_query import bp as protheus_generic_query_bp

_STATIC_DIR = pathlib.Path(__file__).parent.parent / "src" / "static"

_OPENAPI_URL = os.environ.get("OPENAPI_URL", "")
_OPENAPI_TITLE = "Portal Operacional Protheus"
_OPENAPI_DESCRIPTION = (
    "API de backend do **Portal Operacional Protheus**, hospedada como Azure Function "
    "e autenticada via Microsoft Entra ID.\n\n"
    "Permite que usuários internos consultem informações operacionais em tempo real "
    "(produção, logística, estoque, pedidos e demais processos) sem necessidade de "
    "acesso direto ao ERP, ao banco de dados ou a relatórios manuais."
)

app = func.FunctionApp()
app.register_functions(protheus_get_table_columns_bp)
app.register_functions(protheus_generic_query_bp)

@app.function_name(name="openapi_json")
@app.route(route="openapi.json", auth_level=func.AuthLevel.FUNCTION, methods=["GET"])
def openapi_json(req: func.HttpRequest) -> func.HttpResponse:
    return func.HttpResponse(
        get_openapi_json(title=_OPENAPI_TITLE, description=_OPENAPI_DESCRIPTION),
        mimetype="application/json",
    )

@app.function_name(name="swagger_ui")
@app.route(route="docs/swagger", auth_level=func.AuthLevel.FUNCTION, methods=["GET"])
def swagger_ui(req: func.HttpRequest) -> func.HttpResponse:
    return render_swagger_ui(openapi_url=_OPENAPI_URL)

@app.function_name(name="scalar_js")
@app.route(route="static/scalar.js", auth_level=func.AuthLevel.ANONYMOUS, methods=["GET"])
def scalar_js(req: func.HttpRequest) -> func.HttpResponse:
    return func.HttpResponse(
        (_STATIC_DIR / "standalone.js").read_bytes(),
        mimetype="application/javascript",
    )

@app.function_name(name="scalar_ui")
@app.route(route="docs", auth_level=func.AuthLevel.FUNCTION, methods=["GET"])
def scalar_ui(req: func.HttpRequest) -> func.HttpResponse:
    html = f"""<!doctype html>
<html>
  <head>
    <title>{_OPENAPI_TITLE} — API Reference</title>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
  </head>
  <body>
    <script id="api-reference" data-url="/{_OPENAPI_URL}"></script>
    <script src="/api/static/scalar.js"></script>
  </body>
</html>"""
    return func.HttpResponse(html, mimetype="text/html")
