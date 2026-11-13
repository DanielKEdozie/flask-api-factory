from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from flask import Response, jsonify, request
from werkzeug.exceptions import HTTPException

from flask_api_factory.docs_ui import redoc_page, swagger_page
from flask_api_factory.errors import ApiError
from flask_api_factory.openapi import OpenAPISpec
from flask_api_factory.resource import ApiResource
from flask_api_factory.routing import normalize_prefix


class ResourceRegistration:
    """A registered resource and the factory context used to nest children."""

    def __init__(
        self,
        factory: "ApiFactory",
        resource: type,
        path: str,
        endpoint: str,
        pk_field: str,
        pk_type: str,
        one_to_one: bool = False,
    ):
        self.factory = factory
        self.resource = resource
        self.path = path
        self.endpoint = endpoint
        self.pk_field = pk_field
        self.pk_type = pk_type
        self.one_to_one = one_to_one

    def nested_resource(
        self,
        resource: type,
        *,
        path: str,
        fk: Optional[str] = None,
        defaults: Optional[Dict[str, Any]] = None,
        param_name: Optional[str] = None,
        endpoint: Optional[str] = None,
        one_to_one: bool = False,
        **rule_options,
    ) -> "ResourceRegistration":
        """Register a child resource beneath this resource's detail route."""
        return self.factory._register_nested_resource(
            parent=self,
            resource=resource,
            path=path,
            fk=fk,
            defaults=defaults,
            param_name=param_name,
            endpoint=endpoint,
            one_to_one=one_to_one,
            **rule_options,
        )

    def __getattr__(self, name):
        """Delegate resource attributes for callers migrating from class returns."""
        return getattr(self.resource, name)


class ApiFactory:
    """Register ``ApiResource`` classes onto a Flask app or Blueprint.

    ``ApiFactory`` is the framework's entry point. It owns the URL rules, binds
    a ``SQLAlchemy`` instance to every registered resource, and keeps error
    responses JSON. It works with a ``Flask`` app or a ``Blueprint`` (both
    expose ``add_url_rule`` / ``register_error_handler``).

    Usage::

        api = ApiFactory(app, db=db, url_prefix="/api")
        users = api.register_resource(UserResource, path="/users")
        users.nested_resource(
            ProfileResource, path="profile", fk="user_id", one_to_one=True
        )
        api.register_resource(LoginResource, urls=["/auth/login"])  # explicit URLs
    """

    # Collection URLs expose exactly one creation verb, detail URLs target a
    # single row and never accept POST.
    _COLLECTION_VERBS = ("GET", "POST")
    _DETAIL_VERBS = ("GET", "PUT", "PATCH", "DELETE")

    def __init__(
        self,
        target,
        db=None,
        *,
        url_prefix: Optional[str] = None,
        docs: Optional[Dict[str, Any]] = None,
        json_errors: bool = True,
        openapi_url: Optional[str] = "/openapi.json",
        docs_url: Optional[str] = "/docs",
        ui: str = "swagger",
    ):
        self._target = target
        self._db = db
        self._url_prefix = ("/" + (url_prefix or "").strip("/")).rstrip("/") if url_prefix else ""
        self._docs = docs
        self._resources: List[type] = []
        self._registrations: List[ResourceRegistration] = []
        self._openapi_spec = OpenAPISpec(docs=docs)
        self._openapi_url = openapi_url
        self._docs_url = docs_url
        self._ui = ui
        if openapi_url is not None:
            self._add_rule(
                self._join_url(openapi_url),
                f"openapi_{id(self)}",
                self._serve_openapi,
                methods=["GET"],
            )
        if docs_url is not None:
            self._add_rule(
                self._join_url(docs_url),
                f"docs_{id(self)}",
                self._serve_docs,
                methods=["GET"],
            )
        if json_errors:
            self._install_json_error_handler()

    # ---- accessors -----------------------------------------------------

    @property
    def resources(self) -> List[type]:
        """The resource classes registered so far."""
        return list(self._resources)

    @property
    def openapi_spec(self) -> OpenAPISpec:
        """The OpenAPI document generator for this factory."""
        return self._openapi_spec

    def _join_url(self, url: str) -> str:
        url = url.strip("/")
        return f"{self._url_prefix}/{url}" if self._url_prefix else f"/{url}"

    def _add_rule(self, rule, endpoint, view_func, **options):
        self._target.add_url_rule(rule, endpoint=endpoint, view_func=view_func, **options)

    def _serve_openapi(self):
        return jsonify(self._openapi_spec.to_dict()), 200

    def _serve_docs(self):
        spec_url = self._join_url(self._openapi_url) if self._openapi_url else "/openapi.json"
        title = self._openapi_spec.title
        if self._ui == "redoc":
            html = redoc_page(title, spec_url)
        else:
            html = swagger_page(title, spec_url)
        return Response(html, mimetype="text/html")

    def docs(self, *, title=None, version=None, summary=None, description=None,
             contact=None, servers=None, tags=None, security_schemes=None,
             security=None, ui=None):
        """Configure the API docs in place and return ``self`` for chaining.

        Example::

            api = ApiFactory(app, db=db, url_prefix="/api")
            api.docs(title="My API", version="1.0.0", ui="swagger")
            api.register_resource(UserResource, path="/users")

        ``ui`` is ``"swagger"`` (default) or ``"redoc"``. The HTML page and
        JSON spec URLs are set via the constructor (``docs_url=`` /
        ``openapi_url=``), since routes are registered at construction time.
        """
        spec = self._openapi_spec
        if title is not None:
            spec.title = title
        if version is not None:
            spec.version = version
        if summary is not None:
            spec.summary = summary
        if description is not None:
            spec.description = description
        if contact is not None:
            spec.contact = contact
        if servers is not None:
            spec.servers = servers
        if tags is not None:
            spec.tags = tags
        if security_schemes is not None:
            spec.security_schemes = security_schemes
        if security is not None:
            spec.security = security
        if ui is not None:
            if ui not in ("swagger", "redoc"):
                raise ValueError(f"ui must be 'swagger' or 'redoc', got {ui!r}")
            self._ui = ui
        return self

    # ---- registration --------------------------------------------------

    def register(self, resource, **kwargs):
        """Alias for :meth:`register_resource`."""
        return self.register_resource(resource, **kwargs)

    def register_resource(self, resource, urls: Optional[Iterable[str]] = None,
                          path: Optional[str] = None,
                          endpoint: Optional[str] = None,
                          params=None,
                          detail: Optional[bool] = None,
                          **rule_options):
        """Register ``resource`` on the factory's target.

        - ``urls``   explicit URL rules; when given they win over ``path``.
        - ``path``   collection path; required unless ``urls`` is given. A
                     detail route is derived from it when ``detail`` is true.
        - ``endpoint``  defaults to the lowercased class name. Extra URLs get
                     an ``_detail`` suffix.
        - ``params``  URL prefix to nest the resource under, either a single
                     string (``"/fleets/<int:fleet_id>"``) or a list of
                     ``(base, converter)`` tuples
                     (``[("/fleets", "<int:fleet_id>")]``). Leading and
                     trailing slashes on every segment are normalized.
        - ``detail`` defaults to ``True`` for ``ApiResource`` subclasses (they
                     have ``pk_field``) and ``False`` otherwise.

        Derived routes get split verbs when a detail route is generated: the
        collection URL allows at most ``GET, POST`` and the detail URL at most
        ``GET, PUT, PATCH, DELETE``, intersected with the verbs the resource
        actually defines. A single URL (``detail=False``) and explicit ``urls``
        are neither collection nor detail, so they keep every verb the resource
        defines unless you pass ``methods=`` yourself.

        Returns a :class:`ResourceRegistration`; use ``registration.resource``
        to access the registered resource class. The handle can register
        nested resources with :meth:`ResourceRegistration.nested_resource`.
        """
        declared_parent = getattr(resource, "parent_resource", None)
        if declared_parent is not None and urls is None and params is None:
            parent = self._find_registration(declared_parent)
            return self._register_nested_resource(
                parent=parent,
                resource=resource,
                path=path,
                fk=getattr(resource, "parent_fk", None),
                defaults=getattr(resource, "parent_defaults", None),
                param_name=getattr(resource, "parent_param", None),
                endpoint=endpoint,
                one_to_one=getattr(resource, "one_to_one", False),
                detail=detail,
                **rule_options,
            )

        is_api_resource = isinstance(resource, type) and issubclass(resource, ApiResource)

        if is_api_resource and self._db is not None:
            resource.db = self._db

        ep = endpoint or resource.__name__.lower()

        defined = set(getattr(resource, "methods", None) or ())

        if urls is None:
            base = self._base_path(resource, path, params)
            registration_path = base
            want_detail = is_api_resource if detail is None else detail
            if want_detail:
                pk_field = getattr(resource, "pk_field", "id")
                pk_type = getattr(resource, "pk_type", "int")
                detail_url = f"{base}/<{pk_type}:{pk_field}>"
                rules = [
                    (base, self._verbs(defined, self._COLLECTION_VERBS)),
                    (detail_url, self._verbs(defined, self._DETAIL_VERBS)),
                ]
            else:
                # A single URL is neither collection nor detail, so it keeps
                # every verb the resource defines.
                rules = [(base, None)]
        else:
            rules = [(rule, None) for rule in urls]
            registration_path = rules[0][0] if rules else ""

        blueprint_prefix = getattr(self._target, "url_prefix", None) or ""
        for index, (rule, verbs) in enumerate(rules):
            rule_endpoint = ep if index == 0 else f"{ep}_detail"
            options = dict(rule_options)
            if verbs and "methods" not in options:
                options["methods"] = verbs
            self._add_resource_rule(rule, rule_endpoint, resource, options)
            route_methods = options.get("methods") or getattr(resource, "methods", None) or ()
            self._openapi_spec.add_resource(
                resource,
                rule,
                rule_endpoint,
                route_methods,
                url_prefix=blueprint_prefix,
                detail=(
                    is_api_resource
                    and (
                        getattr(resource, "_nested_one_to_one", False)
                        or f":{getattr(resource, 'pk_field', 'id')}>" in rule
                        or f"<{getattr(resource, 'pk_field', 'id')}>" in rule
                    )
                ),
            )

        self._resources.append(resource)
        registration = ResourceRegistration(
            self,
            resource,
            registration_path,
            ep,
            getattr(resource, "pk_field", "id"),
            getattr(resource, "pk_type", "int"),
            bool(getattr(resource, "_nested_one_to_one", False)),
        )
        self._registrations.append(registration)
        return registration

    def _find_registration(self, resource_or_registration):
        if isinstance(resource_or_registration, ResourceRegistration):
            return resource_or_registration
        if not isinstance(resource_or_registration, type):
            raise ValueError("parent must be a registered resource class or registration handle")
        matches = [
            registration for registration in self._registrations
            if registration.resource is resource_or_registration
        ]
        if not matches:
            matches = [
                registration for registration in self._registrations
                if issubclass(registration.resource, resource_or_registration)
            ]
        if not matches:
            raise ValueError(
                f"Parent resource {getattr(resource_or_registration, '__name__', resource_or_registration)!r} "
                "must be registered before its nested resource"
            )
        return matches[-1]

    def nested_resource(
        self,
        resource: type,
        *,
        parent,
        path: str,
        fk: Optional[str] = None,
        defaults: Optional[Dict[str, Any]] = None,
        param_name: Optional[str] = None,
        endpoint: Optional[str] = None,
        one_to_one: bool = False,
        **rule_options,
    ) -> ResourceRegistration:
        """Register a nested resource with an explicitly selected parent.

        ``fk`` names the child's parent foreign-key field. ``defaults`` may
        map child fields to attributes on the parent (for example
        ``{"user_id": "id"}``). A one-to-one resource uses a singular URL
        without a child primary-key segment.
        """
        return self._register_nested_resource(
            parent=self._find_registration(parent),
            resource=resource,
            path=path,
            fk=fk,
            defaults=defaults,
            param_name=param_name,
            endpoint=endpoint,
            one_to_one=one_to_one,
            **rule_options,
        )

    def _register_nested_resource(
        self,
        *,
        parent: ResourceRegistration,
        resource: type,
        path: Optional[str],
        fk: Optional[str],
        defaults: Optional[Dict[str, Any]],
        param_name: Optional[str],
        endpoint: Optional[str],
        one_to_one: bool,
        detail: Optional[bool] = None,
        **rule_options,
    ) -> ResourceRegistration:
        if not path:
            raise ValueError(f"{resource.__name__}: 'path' is required for a nested resource")
        if not isinstance(resource, type) or not issubclass(resource, ApiResource):
            raise TypeError("Nested resources must subclass ApiResource")

        defaults = dict(defaults or {})
        if fk is None and len(defaults) == 1:
            fk = next(iter(defaults))
        if fk is None:
            raise ValueError(
                "Provide fk='parent_id', or a single-field defaults mapping; "
                "specify fk when defaults contains multiple fields"
            )

        param = param_name or fk
        parent_model = getattr(parent.resource, "model", None)
        if parent_model is None:
            raise ValueError(f"{parent.resource.__name__} has no model for parent lookup")
        parent_pk_field = parent.pk_field
        if getattr(parent_model, parent_pk_field, None) is None:
            raise ValueError(
                f"{parent.resource.__name__} has no primary-key field {parent_pk_field!r}"
            )
        child_model = getattr(resource, "model", None)
        if child_model is None:
            raise ValueError(f"{resource.__name__} has no model for foreign-key scoping")
        for field in {fk, *defaults.keys()}:
            if getattr(child_model, field, None) is None:
                raise ValueError(
                    f"{resource.__name__} has no model field {field!r} for nested scoping"
                )
        for source in defaults.values():
            if isinstance(source, str) and getattr(parent_model, source, None) is None:
                raise ValueError(
                    f"{parent.resource.__name__} has no parent attribute {source!r}"
                )
        if not parent.one_to_one and param in (
            getattr(parent.resource, "route_params", {}) or {}
        ):
            raise ValueError(
                f"Nested route parameter {param!r} conflicts with an ancestor parameter"
            )
        if not parent.one_to_one and param == getattr(resource, "pk_field", "id"):
            raise ValueError(
                f"Nested route parameter {param!r} conflicts with the child primary key"
            )
        if one_to_one and detail:
            raise ValueError("one_to_one resources cannot register a child detail route")
        db = self._db
        if db is None:
            raise RuntimeError("A db instance is required to register nested ApiResource resources")

        ancestor_contexts = tuple(
            getattr(parent.resource, "_nested_parent_contexts", ()) or ()
        )

        def context_parent(context):
            context_param = context["param"]
            if context_param is not None:
                raw = (request.view_args or {}).get(context_param)
                if raw is None:
                    return None
                rules = getattr(parent.resource, "route_params", {}) or {}
                coerce = rules.get(context_param, {}).get("type", str)
                try:
                    value = coerce(raw)
                except (TypeError, ValueError):
                    raise ApiError(
                        400, f"Invalid value for URL parameter '{context_param}'"
                    ) from None
                resolver = (getattr(parent.resource, "resolve_params", {}) or {}).get(
                    context_param
                )
                if resolver is None:
                    return value
                result = resolver(value)
                if isinstance(result, tuple):
                    payload, status = result
                    message = (
                        payload.get("message", "Error")
                        if isinstance(payload, dict)
                        else str(payload)
                    )
                    errors = payload.get("errors") if isinstance(payload, dict) else None
                    raise ApiError(status, message, errors=errors)
                return result

            model = context["parent_model"]
            query = db.session.query(model)
            for ancestor in context["ancestors"]:
                ancestor_obj = context_parent(ancestor)
                if ancestor_obj is None:
                    return None
                defaults_for_ancestor = dict(ancestor["defaults"])
                defaults_for_ancestor.setdefault(
                    ancestor["fk"], ancestor["pk_field"]
                )
                source = defaults_for_ancestor[ancestor["fk"]]
                if callable(source):
                    value = source(ancestor_obj)
                elif isinstance(source, str) and hasattr(ancestor_obj, source):
                    value = getattr(ancestor_obj, source)
                else:
                    value = source
                column = getattr(model, ancestor["fk"], None)
                if column is not None:
                    query = query.filter(column == value)
            return query.first()

        def resolve_parent(value):
            parent_pk_column = getattr(parent_model, parent_pk_field, None)
            if parent_pk_column is None:
                raise ValueError(
                    f"{parent.resource.__name__} has no primary-key field {parent_pk_field!r}"
                )
            query = db.session.query(parent_model).filter(parent_pk_column == value)
            for context in ancestor_contexts:
                ancestor_obj = context_parent(context)
                if ancestor_obj is None:
                    return None
                defaults_for_context = dict(context["defaults"])
                defaults_for_context.setdefault(context["fk"], context["pk_field"])
                source = defaults_for_context[context["fk"]]
                if callable(source):
                    parent_value = source(ancestor_obj)
                elif isinstance(source, str) and hasattr(ancestor_obj, source):
                    parent_value = getattr(ancestor_obj, source)
                else:
                    parent_value = source
                column = getattr(parent_model, context["fk"], None)
                if column is not None:
                    query = query.filter(column == parent_value)
            return query.first()

        param = None if parent.one_to_one else param
        route_params = {
            **dict(getattr(parent.resource, "route_params", {}) or {}),
            **dict(getattr(resource, "route_params", {}) or {}),
        }
        resolve_params = {
            **dict(getattr(parent.resource, "resolve_params", {}) or {}),
            **dict(getattr(resource, "resolve_params", {}) or {}),
        }
        if param is not None:
            pk_column = getattr(parent_model, parent_pk_field)
            try:
                pk_python_type = pk_column.type.python_type
            except (AttributeError, NotImplementedError):
                pk_python_type = int if parent.pk_type == "int" else str
            route_params[param] = {
                "type": pk_python_type,
                "required": True,
            }
            resolve_params[param] = resolve_parent

        nested_contexts = list(ancestor_contexts)
        nested_contexts.append({
            "param": param,
            "fk": fk,
            "defaults": defaults,
            "pk_field": parent_pk_field,
            "parent_model": parent_model,
            "ancestors": tuple(
                getattr(parent.resource, "_nested_parent_contexts", ()) or ()
            ),
        })
        attrs = {
            "parent_resource": None,
            "route_params": route_params,
            "resolve_params": resolve_params,
            "_nested_parent_contexts": tuple(nested_contexts),
            "_nested_one_to_one": one_to_one,
        }
        registered_resource = type(
            f"{resource.__name__}Nested{len(self._registrations) + 1}",
            (resource,),
            attrs,
        )

        parent_detail_path = (
            parent.path
            if parent.one_to_one
            else f"{parent.path}/<{parent.pk_type}:{param}>"
        )
        nested_path = f"{parent_detail_path}/{path.strip('/')}"
        allowed_detail = detail if detail is not None else not one_to_one
        methods = None
        if one_to_one:
            methods = self._verbs(
                set(getattr(registered_resource, "methods", None) or ()),
                ("GET", "POST", "PUT", "PATCH", "DELETE"),
            )
        register_options = dict(rule_options)
        if methods is not None:
            register_options["methods"] = methods
        registration = self.register_resource(
            registered_resource,
            path=nested_path.removeprefix(self._url_prefix) if self._url_prefix else nested_path,
            endpoint=endpoint or f"{parent.endpoint}_{path.strip('/').replace('/', '_')}",
            detail=allowed_detail,
            **register_options,
        )
        return registration

    # ---- internals -----------------------------------------------------

    @staticmethod
    def _verbs(defined, allowed) -> List[str]:
        """Verbs ``resource`` defines, restricted to ``allowed``."""
        return [verb for verb in allowed if verb in defined]

    def _base_path(self, resource, path: Optional[str],
                   params=None) -> str:
        """Build the collection URL: url prefix + params prefix + path."""
        if not path:
            raise ValueError(
                f"{resource.__name__}: 'path' is required; pass path= or urls= "
                f"to register_resource()"
            )

        return f"{self._url_prefix}{normalize_prefix(params)}/{path.strip('/')}"

    def _add_resource_rule(self, rule, endpoint, resource, options):
        self._target.add_url_rule(
            rule, endpoint=endpoint, view_func=resource.as_view(endpoint), **options
        )

    def _install_json_error_handler(self):
        def api_error(exc: ApiError):
            return exc.to_dict(), exc.status_code

        def handler(exc: HTTPException):
            return {"message": exc.description or exc.name}, exc.code

        def unhandled(exc: Exception):
            return {"message": "Internal server error"}, 500

        self._target.register_error_handler(ApiError, api_error)
        self._target.register_error_handler(HTTPException, handler)
        self._target.register_error_handler(Exception, unhandled)
