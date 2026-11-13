from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from flask import request
from flask.views import MethodView
from marshmallow import Schema

from flask_api_factory.errors import ApiError
from flask_api_factory.params import ParamMixin
from flask_api_factory.pagination import PaginationMixin
from flask_api_factory.querying import FilterError, QueryingMixin
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

    Nested resources can be declared with ``parent_resource``, ``parent_fk``,
    and optionally ``parent_defaults`` / ``singleton``; registration then
    resolves the parent class from the factory's registered resources.
    ``one_to_one`` remains a compatibility alias for ``singleton``.
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
    parent_resource = None
    parent_fk: Optional[str] = None
    # Map child model fields to attributes on their nested parent.
    parent_defaults: Optional[Dict[str, Any]] = None
    parent_param: Optional[str] = None
    singleton: bool = False
    # Deprecated class-level alias retained for backwards compatibility.
    one_to_one: Optional[bool] = None

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
            self._resolve_url_params(kwargs)

    def _nested_parent_object(self, context):
        param = context["param"]
        if param is not None:
            return self._resolved.get(param)

        model = context["parent_model"]
        query = self.db.session.query(model)
        for ancestor in context["ancestors"]:
            parent = self._nested_parent_object(ancestor)
            if parent is None:
                return None
            defaults = dict(ancestor["defaults"])
            defaults.setdefault(ancestor["fk"], ancestor["pk_field"])
            source = defaults[ancestor["fk"]]
            if callable(source):
                value = source(parent)
            elif isinstance(source, str) and hasattr(parent, source):
                value = getattr(parent, source)
            else:
                value = source
            column = getattr(model, ancestor["fk"], None)
            if column is not None:
                query = query.filter(column == value)
        return query.first()

    def _inject_parent_values(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        payload = dict(payload or {})
        contexts = getattr(self, "_nested_parent_contexts", ())
        if contexts:
            context = contexts[-1]
            parent = self._nested_parent_object(context)
            if parent is None:
                raise ApiError(404, "Parent resource not found")
            defaults = dict(context["defaults"])
            defaults.setdefault(context["fk"], context["pk_field"])
            for field, source in defaults.items():
                if callable(source):
                    payload[field] = source(parent)
                elif isinstance(source, str) and hasattr(parent, source):
                    payload[field] = getattr(parent, source)
                else:
                    payload[field] = source

        for name, obj in getattr(self, "_resolved", {}).items():
            if name in payload:
                continue
            if hasattr(obj, "id") and getattr(self.model, name, None) is not None:
                payload[name] = obj.id
            elif hasattr(self.model, name):
                payload[name] = getattr(obj, "id", obj)
        return payload

    def _singleton_query(self):
        try:
            return self._build_query()
        except FilterError as exc:
            raise ApiError(400, str(exc)) from exc

    def _nested_parent_query(self):
        contexts = getattr(self, "_nested_parent_contexts", ())
        if not contexts:
            return None

        query = self.db.session.query(self.model)
        for field, value in self._parent_filters().items():
            column = getattr(self.model, field, None)
            if column is not None:
                query = query.filter(column == value)
        return query

    def _collection_get(self):
        try:
            query = self._build_query()
        except FilterError as exc:
            raise ApiError(400, str(exc)) from exc
        page, per_page = self._pagination_params()
        paginate = self._should_paginate()
        return self.service.list_all(
            query=query,
            paginate=paginate,
            page=page,
            per_page=per_page,
        )

    def _detail_get(self, pk):
        try:
            query = self._build_query()
        except FilterError as exc:
            raise ApiError(400, str(exc)) from exc
        return self.service.retrieve(pk, query=query, pk_field=self.pk_field)

    def get(self, **kwargs):
        self._resolve_kwargs(**kwargs)
        singleton = getattr(self, "_nested_singleton", False)
        pk = None if singleton else kwargs.get(self.pk_field)
        if singleton:
            query = self._singleton_query()
            obj = query.first()
            if obj is None:
                raise ApiError(404, f"{self.model.__name__} not found")
            return self.service.retrieve(
                getattr(obj, self.pk_field), query=query, pk_field=self.pk_field
            )
        if pk is None:
            return self._collection_get()
        return self._detail_get(pk)

    def post(self, **kwargs):
        if not self.allow_create:
            raise ApiError(405, "Method not allowed")
        self._resolve_kwargs(**kwargs)
        payload = self._inject_parent_values(request.get_json(silent=True) or {})
        return self.service.create(payload)

    def put(self, **kwargs):
        if not self.allow_update:
            raise ApiError(405, "Method not allowed")
        self._resolve_kwargs(**kwargs)
        singleton = getattr(self, "_nested_singleton", False)
        query = self._nested_parent_query()
        pk = None if singleton else kwargs.get(self.pk_field)
        if pk is None:
            if singleton:
                if query is None:
                    query = self._singleton_query()
                obj = query.first()
                if obj is None:
                    raise ApiError(404, f"{self.model.__name__} not found")
                pk = getattr(obj, self.pk_field)
                payload = self._inject_parent_values(request.get_json(silent=True) or {})
                return self.service.update(
                    pk, payload, pk_field=self.pk_field, partial=False, query=query
                )
            raise ApiError(400, f"Missing {self.pk_field}")
        payload = self._inject_parent_values(request.get_json(silent=True) or {})
        return self.service.update(
            pk, payload, pk_field=self.pk_field, partial=False, query=query
        )

    def patch(self, **kwargs):
        if not self.allow_update:
            raise ApiError(405, "Method not allowed")
        self._resolve_kwargs(**kwargs)
        singleton = getattr(self, "_nested_singleton", False)
        query = self._nested_parent_query()
        pk = None if singleton else kwargs.get(self.pk_field)
        if pk is None:
            if singleton:
                if query is None:
                    query = self._singleton_query()
                obj = query.first()
                if obj is None:
                    raise ApiError(404, f"{self.model.__name__} not found")
                pk = getattr(obj, self.pk_field)
                payload = self._inject_parent_values(request.get_json(silent=True) or {})
                return self.service.update(
                    pk, payload, pk_field=self.pk_field, partial=True, query=query
                )
            raise ApiError(400, f"Missing {self.pk_field}")
        payload = self._inject_parent_values(request.get_json(silent=True) or {})
        return self.service.update(
            pk, payload, pk_field=self.pk_field, partial=True, query=query
        )

    def delete(self, **kwargs):
        if not self.allow_delete:
            raise ApiError(405, "Method not allowed")
        self._resolve_kwargs(**kwargs)
        singleton = getattr(self, "_nested_singleton", False)
        query = self._nested_parent_query()
        pk = None if singleton else kwargs.get(self.pk_field)
        if pk is None:
            if singleton:
                if query is None:
                    query = self._singleton_query()
                obj = query.first()
                if obj is None:
                    raise ApiError(404, f"{self.model.__name__} not found")
                pk = getattr(obj, self.pk_field)
                return self.service.delete(pk, pk_field=self.pk_field, query=query)
            raise ApiError(400, f"Missing {self.pk_field}")
        return self.service.delete(pk, pk_field=self.pk_field, query=query)
