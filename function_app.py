import logging
import json
import azure.functions as func
from pydantic import BaseModel
from azure_functions_openapi import (
    get_openapi_json,
    get_openapi_yaml,
    openapi,
    render_swagger_ui,
)
from blueprints.protheus_get_table_columns import bp as protheus_get_table_columns_bp
from blueprints.protheus_generic_query import bp as protheus_generic_query_bp

app = func.FunctionApp()
app.register_functions(protheus_get_table_columns_bp)
app.register_functions(protheus_generic_query_bp)

@app.function_name(name="openapi_json")
@app.route(route="openapi.json", auth_level=func.AuthLevel.FUNCTION, methods=["GET"])
def openapi_json(req: func.HttpRequest) -> func.HttpResponse:
    return func.HttpResponse(
        get_openapi_json(
            title="Portal Operacional Protheus",
            description=(
            "API de backend do **Portal Operacional Protheus**, hospedada como Azure Function e autenticada via Microsoft Entra ID.\n\n"
            "Permite que usuários internos consultem informações operacionais em tempo real (produção, logística, estoque, "
            "pedidos e demais processos) sem necessidade de acesso direto ao ERP, ao banco de dados ou a relatórios manuais."
        ),
        ),
        mimetype="application/json",
    )

@app.function_name(name="openapi_yaml")
@app.route(route="openapi.yaml", auth_level=func.AuthLevel.FUNCTION, methods=["GET"])
def openapi_yaml(req: func.HttpRequest) -> func.HttpResponse:
    return func.HttpResponse(
        get_openapi_yaml(
            title="Portal Operacional Protheus",
            description=(
            "API de backend do **Portal Operacional Protheus**, hospedada como Azure Function e autenticada via Microsoft Entra ID.\n\n"
            "Permite que usuários internos consultem informações operacionais em tempo real (produção, logística, estoque, "
            "pedidos e demais processos) sem necessidade de acesso direto ao ERP, ao banco de dados ou a relatórios manuais."
        ),
        ),
        mimetype="application/x-yaml",
    )

@app.function_name(name="swagger_ui")
@app.route(route="docs", auth_level=func.AuthLevel.FUNCTION, methods=["GET"])
def swagger_ui(req: func.HttpRequest) -> func.HttpResponse:
    return render_swagger_ui()