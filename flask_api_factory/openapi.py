"""OpenAPI specification generation for flask-api-factory."""

import re
from typing import Any, Dict, Iterable, List, Optional, Tuple, Type

from marshmallow import Schema, fields


_PATH_PARAMETER = re.compile(r"<(?:(?P<converter>[^:<>]+):)?(?P<name>[^<>]+)>")
_FIELD_TYPES = {
    "String": ("string", None),
    "Email": ("string", "email"),
    "URL": ("string", "uri"),
    "UUID": ("string", "uuid"),
    "Integer": ("integer", "int32"),
    "Boolean": ("boolean", None),
    "Float": ("number", "float"),
    "Decimal": ("number", None),
    "Date": ("string", "date"),
    "DateTime": ("string", "date-time"),
    "Time": ("string", "time"),
}
_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head"}


class OpenAPISpec:
    """Build an OpenAPI 3.0.3 document from registered resources."""

    def __init__(
        self,
        docs: Optional[Dict[str, Any]] = None,
        title: str = "API",
        version: str = "1.0.0",
        summary: Optional[str] = None,
        contact: Optional[Any] = None,
        description: Optional[str] = None,
        servers: Optional[List[Any]] = None,
        tags: Optional[List[Dict[str, Any]]] = None,
        url_prefix: str = "",
    ):
        self.docs = docs or {}
        self.title = self.docs.get("title", title)
        self.version = self.docs.get("version", version)
        self.summary = self.docs.get("summary", summary)
        self.contact = self.docs.get("contact", contact)
        self.description = self.docs.get("description", description)
        self.servers = self.docs.get("servers", servers) or []
        self.tags = self.docs.get("tags", tags) or []
        self.url_prefix = self.docs.get("url_prefix", url_prefix)
        self._paths: Dict[str, Any] = {}
        self._components: Dict[str, Any] = {"schemas": {}}
        self._schema_names: Dict[Tuple[Type[Schema], bool], str] = {}

    def _normalize_contact(self) -> Optional[Dict[str, Any]]:
        if self.contact is None:
            return None
        if isinstance(self.contact, dict):
            return self.contact
        return {"email": self.contact} if "@" in self.contact else {"name": self.contact}

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable OpenAPI document."""
        info: Dict[str, Any] = {"title": self.title, "version": self.version}
        if self.description is not None or self.summary is not None:
            info["description"] = self.description or self.summary
        contact = self._normalize_contact()
        if contact is not None:
            info["contact"] = contact

        document: Dict[str, Any] = {
            "openapi": "3.0.3",
            "info": info,
            "paths": self._paths,
        }
        if self.servers:
            document["servers"] = [
                {"url": server} if isinstance(server, str) else server
                for server in self.servers
            ]
        if self.tags:
            document["tags"] = self.tags
        components = {
            key: value
            for key, value in self._components.items()
            if value
        }
        if components:
            document["components"] = components
        return document

    @staticmethod
    def _path_and_parameters(rule: str) -> tuple[str, List[Dict[str, Any]]]:
        parameters = []

        def replace_parameter(match):
            name = match.group("name")
            converter = match.group("converter") or "string"
            schema_type, schema_format = {
                "int": ("integer", "int32"),
                "float": ("number", "float"),
                "uuid": ("string", "uuid"),
                "path": ("string", None),
                "string": ("string", None),
            }.get(converter, ("string", None))
            schema: Dict[str, Any] = {"type": schema_type}
            if schema_format:
                schema["format"] = schema_format
            parameters.append({
                "name": name,
                "in": "path",
                "required": True,
                "schema": schema,
            })
            return "{" + name + "}"

        path = _PATH_PARAMETER.sub(replace_parameter, rule)
        return path or "/", parameters

    @staticmethod
    def _schema_class(resource_class: Type) -> Optional[Type[Schema]]:
        schema_class = getattr(resource_class, "schema", None)
        if isinstance(schema_class, type) and issubclass(schema_class, Schema):
            return schema_class
        if isinstance(schema_class, Schema):
            return type(schema_class)
        return None

    def _schema_reference(
        self,
        schema_class: Type[Schema],
        partial: bool = False,
    ) -> Dict[str, str]:
        cache_key = (schema_class, partial)
        name = self._schema_names.get(cache_key)
        if name is None:
            name = f"{schema_class.__name__}Patch" if partial else schema_class.__name__
            existing = self._components["schemas"].get(name)
            if existing is not None:
                name = f"{schema_class.__module__.replace('.', '_')}_{name}"
            self._schema_names[cache_key] = name
            self._components["schemas"][name] = self._schema_definition(
                schema_class,
                partial=partial,
            )
        return {"$ref": f"#/components/schemas/{name}"}

    def _field_schema(self, field: fields.Field) -> Dict[str, Any]:
        if isinstance(field, fields.Nested):
            nested_schema = field.schema
            schema = self._schema_reference(type(nested_schema))
        elif isinstance(field, fields.List):
            schema = {"type": "array", "items": self._field_schema(field.inner)}
        elif isinstance(field, fields.Dict):
            schema = {"type": "object"}
            if field.value_field is not None:
                schema["additionalProperties"] = self._field_schema(field.value_field)
        elif isinstance(field, fields.Raw):
            schema = {}
        else:
            field_name = type(field).__name__
            schema_type, schema_format = _FIELD_TYPES.get(field_name, ("string", None))
            schema = {"type": schema_type}
            if schema_format:
                schema["format"] = schema_format
            choices = getattr(field, "choices", None)
            if choices:
                schema["enum"] = list(choices)

        if getattr(field, "allow_none", False):
            schema["nullable"] = True
        if getattr(field, "dump_only", False):
            schema["readOnly"] = True
        if getattr(field, "load_only", False):
            schema["writeOnly"] = True
        return schema

    def _schema_definition(
        self,
        schema_class: Type[Schema],
        partial: bool = False,
    ) -> Dict[str, Any]:
        schema = schema_class()
        properties = {
            name: self._field_schema(field)
            for name, field in schema.fields.items()
        }
        required = [
            name for name, field in schema.fields.items()
            if field.required and not field.dump_only
        ]
        definition: Dict[str, Any] = {"type": "object", "properties": properties}
        if required and not partial:
            definition["required"] = required
        return definition

    @staticmethod
    def _operation_tags(resource_class: Type) -> List[str]:
        tags = getattr(resource_class, "openapi_tags", None) or []
        return [
            tag if isinstance(tag, str) else tag["name"]
            for tag in tags
            if isinstance(tag, str) or (isinstance(tag, dict) and "name" in tag)
        ]

    def _query_parameters(self, resource_class: Type, detail: bool) -> List[Dict[str, Any]]:
        if detail or not hasattr(resource_class, "filter_fields"):
            return []

        parameters = []
        for field_name in getattr(resource_class, "filter_fields", []) or []:
            parameters.append({
                "name": field_name,
                "in": "query",
                "required": False,
                "schema": {"type": "string"},
            })
        if getattr(resource_class, "sort_fields", None):
            parameters.append({
                "name": "sort",
                "in": "query",
                "required": False,
                "schema": {
                    "type": "string",
                    "enum": list(resource_class.sort_fields),
                },
            })
            parameters.append({
                "name": "order",
                "in": "query",
                "required": False,
                "schema": {"type": "string", "enum": ["asc", "desc"]},
            })
        parameters.append({
            "name": "paginate",
            "in": "query",
            "required": False,
            "schema": {"type": "boolean"},
        })
        parameters.extend([
            {
                "name": "page",
                "in": "query",
                "required": False,
                "schema": {"type": "integer", "minimum": 1},
            },
            {
                "name": "per_page",
                "in": "query",
                "required": False,
                "schema": {"type": "integer", "minimum": 1},
            },
        ])
        return parameters

    def _response_schema(self, schema_class: Optional[Type[Schema]], many: bool = False):
        item_schema: Dict[str, Any] = (
            self._schema_reference(schema_class) if schema_class else {}
        )
        if many:
            data_schema = {
                "oneOf": [
                    {"type": "array", "items": item_schema},
                    {
                        "type": "object",
                        "properties": {
                            "items": {"type": "array", "items": item_schema},
                            "total": {"type": "integer"},
                            "pages": {"type": "integer"},
                            "page": {"type": "integer"},
                            "per_page": {"type": "integer"},
                        },
                        "required": ["items", "total", "pages", "page", "per_page"],
                    },
                ]
            }
        else:
            data_schema = item_schema
        return {
            "type": "object",
            "properties": {"data": data_schema},
            "required": ["data"],
        }

    def _operation(
        self,
        resource_class: Type,
        endpoint: str,
        method: str,
        path_parameters: List[Dict[str, Any]],
        detail: bool,
    ) -> Dict[str, Any]:
        method = method.lower()
        operation: Dict[str, Any] = {
            "operationId": re.sub(r"\W+", "_", f"{endpoint}_{method}").strip("_"),
            "responses": {},
        }

        tags = self._operation_tags(resource_class)
        if tags:
            operation["tags"] = tags
        summary = getattr(resource_class, "openapi_summary", None)
        description = getattr(resource_class, "openapi_description", None)
        if summary:
            operation["summary"] = summary
        if description:
            operation["description"] = description

        schema_class = self._schema_class(resource_class)
        parameters = list(path_parameters)
        parameters.extend(self._query_parameters(resource_class, detail))
        if parameters:
            operation["parameters"] = parameters

        if method in {"post", "put", "patch"} and schema_class is not None:
            schema_ref = self._schema_reference(
                schema_class,
                partial=method == "patch",
            )
            operation["requestBody"] = {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": schema_ref,
                    }
                },
            }

        if method == "delete":
            operation["responses"]["204"] = {"description": "Resource deleted"}
        else:
            status = "201" if method == "post" else "200"
            response_schema = self._response_schema(
                schema_class,
                many=method == "get" and not detail,
            )
            operation["responses"][status] = {
                "description": "Successful response",
                "content": {
                    "application/json": {"schema": response_schema},
                },
            }

        for status, description in (
            ("400", "Invalid request"),
            ("404", "Resource not found"),
            ("422", "Validation error"),
        ):
            operation["responses"].setdefault(status, {
                "description": description,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {
                                "message": {"type": "string"},
                                "errors": {"type": "object"},
                            },
                        }
                    }
                },
            })
        return operation

    def add_resource(
        self,
        resource_class: Type,
        rule: str,
        endpoint: str,
        methods: Iterable[str],
        *,
        url_prefix: Optional[str] = None,
        detail: bool = False,
    ) -> None:
        """Add a registered resource's route and operations to the document."""
        prefix = self.url_prefix if url_prefix is None else url_prefix
        path, path_parameters = self._path_and_parameters(
            f"{prefix.rstrip('/')}/{rule.lstrip('/')}"
        )
        path_item = self._paths.setdefault(path, {})
        for method in methods:
            method = method.lower()
            if method in _HTTP_METHODS:
                path_item[method] = self._operation(
                    resource_class, endpoint, method, path_parameters, detail
                )
