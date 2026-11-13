from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from flask import request
from flask.views import MethodView
from marshmallow import Schema

from flask_api_factory.params import ParamMixin
from flask_api_factory.pagination import PaginationMixin
from flask_api_factory.querying import QueryingMixin
from flask_api_factory.routing import RoutingMixin
from flask_api_factory.service import ApiService


class ApiResource(RoutingMixin, ParamMixin, QueryingMixin, PaginationMixin, MethodView):
    """Flask resource that wires an ApiService over HTTP.

    Functionality is split across focused mixins:

    - RoutingMixin     pk config, params, routes
    - ParamMixin       URL param validation/resolution, parent filters
    - QueryingMixin    filtering, sorting, eager loads, _build_query
    - PaginationMixin  paginate flag, page size, ?paginate= handling

    Subclass, set ``schema`` and ``model``, then register with
    ``ApiFactory(...).register_resource(...)``.
    """

    schema: Optional[type[Schema]] = None
    model = None
    db = None

    allow_create: bool = True
    allow_update: bool = True
    allow_delete: bool = True
    decorators: Optional[List[Callable]] = None
    schema_load_kwargs: Optional[Dict[str, Any]] = None
    schema_dump_kwargs: Optional[Dict[str, Any]] = None

    docs: Optional[dict] = None
    openapi_tags: Optional[List[dict]] = None
    openapi_summary: Optional[str] = None
    openapi_description: Optional[str] = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.db is None or self.schema is None or self.model is None:
            raise RuntimeError(
                f"{self.__class__.__name__}: schema, model, and db must be set before instantiation"
            )

    @property
    def service(self) -> ApiService:
        schema_cls = self.schema
        if schema_cls is None:
            raise RuntimeError(f"{self.__class__.__name__}: schema is not defined")
        if self.model is None:
            raise RuntimeError(f"{self.__class__.__name__}: model is not defined")
        if self.db is None:
            raise RuntimeError(f"{self.__class__.__name__}: db is not bound to this resource")
        return ApiService(
            db=self.db,
            schema=schema_cls(**(self.schema_load_kwargs or {})),
            model=self.model,
            dump_schema=schema_cls(**(self.schema_dump_kwargs or {})),
            hooks={
                "before_load": self.before_load,
                "after_load": self.after_load,
                "before_commit": self.before_commit,
            },
        )

    def before_load(self, payload, instance=None, mode="create"):
        """Hook for resource-specific payload mutation before validation."""

    def after_load(self, instance, data, mode="create"):
        """Hook for resource-specific behavior after schema validation."""

    def before_commit(self, instance, data, mode="create"):
        """Hook for resource-specific work before persistence."""

    def _resolve_kwargs(self, **kwargs):
        self._resolved = {}
        if self.route_params or self.resolve_params:
            error = self._resolve_url_params(kwargs)
            if error is not None:
                return error
        return None

    def _inject_parent_values(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(payload or {})
        for name, obj in getattr(self, "_resolved", {}).items():
            if name in payload:
                continue
            if hasattr(obj, "id") and getattr(self.model, name, None) is not None:
                payload[name] = obj.id
            elif hasattr(self.model, name):
                payload[name] = getattr(obj, "id", obj)
        return payload

    def _collection_get(self):
        query = self._build_query()
        page, per_page = self._pagination_params()
        paginate = self._should_paginate()
        return self.service.list_all(
            query=query,
            paginate=paginate,
            page=page,
            per_page=per_page,
        )

    def _detail_get(self, pk):
        query = self._build_query()
        return self.service.retrieve(pk, query=query, pk_field=self.pk_field)

    def get(self, **kwargs):
        err = self._resolve_kwargs(**kwargs)
        if err is not None:
            return err
        pk = kwargs.get(self.pk_field)
        if pk is None:
            return self._collection_get()
        return self._detail_get(pk)

    def post(self, **kwargs):
        err = self._resolve_kwargs(**kwargs)
        if err is not None:
            return err
        payload = self._inject_parent_values(request.get_json(silent=True) or {})
        return self.service.create(payload)

    def put(self, **kwargs):
        err = self._resolve_kwargs(**kwargs)
        if err is not None:
            return err
        pk = kwargs.get(self.pk_field)
        if pk is None:
            return {"message": f"Missing {self.pk_field}"}, 400
        payload = self._inject_parent_values(request.get_json(silent=True) or {})
        return self.service.update(pk, payload, pk_field=self.pk_field)

    def patch(self, **kwargs):
        return self.put(**kwargs)

    def delete(self, **kwargs):
        err = self._resolve_kwargs(**kwargs)
        if err is not None:
            return err
        pk = kwargs.get(self.pk_field)
        if pk is None:
            return {"message": f"Missing {self.pk_field}"}, 400
        return self.service.delete(pk, pk_field=self.pk_field)
