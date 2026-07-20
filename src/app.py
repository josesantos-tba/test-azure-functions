import logging
import os
import pathlib
import azure.functions as func
from pydantic import BaseModel
from azure_functions_openapi import ( get_openapi_json, openapi, render_swagger_ui )
from src.features.databricks.export_csv import bp as databricks_export_csv_bp
from src.features.databricks.list_tables import bp as databricks_list_tables_bp
from src.features.databricks.query_json import bp as databricks_query_json_bp
from src.features.databricks.table_columns import bp as databricks_table_columns_bp
from src.features.databricks.table_count import bp as databricks_table_count_bp
from src.features.protheus.bloqueio_estoque import bp as protheus_bloqueio_estoque_bp
from src.features.protheus.export_csv import bp as protheus_export_csv_bp
from src.features.protheus.table_columns import bp as protheus_get_table_columns_bp
from src.features.protheus.list_tables import bp as protheus_list_tables_bp
from src.features.protheus.query_json import bp as protheus_query_json_bp
from src.features.protheus.saldo_estoque import bp as protheus_saldo_estoque_bp
from src.features.protheus.table_count import bp as protheus_table_count_bp

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
app.register_functions(databricks_export_csv_bp)
app.register_functions(databricks_list_tables_bp)
app.register_functions(databricks_query_json_bp)
app.register_functions(databricks_table_columns_bp)
app.register_functions(databricks_table_count_bp)
app.register_functions(protheus_bloqueio_estoque_bp)
app.register_functions(protheus_export_csv_bp)
app.register_functions(protheus_get_table_columns_bp)
app.register_functions(protheus_list_tables_bp)
app.register_functions(protheus_query_json_bp)
app.register_functions(protheus_saldo_estoque_bp)
app.register_functions(protheus_table_count_bp)

@app.function_name(name="openapi_json")
@app.route(route="openapi.json", auth_level=func.AuthLevel.ANONYMOUS, methods=["GET"])
def openapi_json(req: func.HttpRequest) -> func.HttpResponse:
    return func.HttpResponse(
        get_openapi_json(title=_OPENAPI_TITLE, description=_OPENAPI_DESCRIPTION),
        mimetype="application/json",
    )

@app.function_name(name="swagger_ui")
@app.route(route="docs/swagger", auth_level=func.AuthLevel.ANONYMOUS, methods=["GET"])
def swagger_ui(req: func.HttpRequest) -> func.HttpResponse:
    return render_swagger_ui(openapi_url="api/openapi.json")

@app.function_name(name="scalar_js")
@app.route(route="static/scalar.js", auth_level=func.AuthLevel.ANONYMOUS, methods=["GET"])
def scalar_js(req: func.HttpRequest) -> func.HttpResponse:
    return func.HttpResponse(
        (_STATIC_DIR / "standalone.js").read_bytes(),
        mimetype="application/javascript",
    )

@app.function_name(name="scalar_ui")
@app.route(route="docs", auth_level=func.AuthLevel.ANONYMOUS, methods=["GET"])
def scalar_ui(req: func.HttpRequest) -> func.HttpResponse:
    html = f"""<!doctype html>
<html>
  <head>
    <title>{_OPENAPI_TITLE} — API Reference</title>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
  </head>
  <body>
    <script id="api-reference" data-url="/api/openapi.json"></script>
    <script src="/api/static/scalar.js"></script>
  </body>
</html>"""
    return func.HttpResponse(html, mimetype="text/html")
