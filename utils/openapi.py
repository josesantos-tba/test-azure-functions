import copy


def inline_refs(schema: dict) -> dict:
    """Resolve $defs/$ref inline so the schema is self-contained for Swagger UI.

    Pydantic's model_json_schema() uses $defs (JSON Schema 2020-12), but OpenAPI
    expects refs to point to #/components/schemas/. When a schema is embedded inline
    in a response object the $ref path cannot be resolved, causing Swagger UI errors.
    This function replaces every $ref with the object it points to inside $defs.
    """
    schema = copy.deepcopy(schema)
    defs = schema.pop("$defs", {})

    def _resolve(obj: object) -> object:
        if isinstance(obj, dict):
            if "$ref" in obj:
                parts = obj["$ref"].lstrip("#/").split("/")
                if parts[0] == "$defs" and parts[1] in defs:
                    return _resolve(copy.deepcopy(defs[parts[1]]))
            return {k: _resolve(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [_resolve(i) for i in obj]
        return obj

    return _resolve(schema)
