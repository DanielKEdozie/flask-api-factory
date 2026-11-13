from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from flask import jsonify
from werkzeug.exceptions import HTTPException

from flask_api_factory.openapi import OpenAPISpec
from flask_api_factory.resource import ApiResource
from flask_api_factory.routing import normalize_prefix


class ApiFactory:
    """Register ``ApiResource`` classes onto a Flask app or Blueprint.

    ``ApiFactory`` is the framework's entry point. It owns the URL rules, binds
    a ``SQLAlchemy`` instance to every registered resource, and keeps error
    responses JSON. It works with a ``Flask`` app or a ``Blueprint`` (both
    expose ``add_url_rule`` / ``register_error_handler``).

    Usage::

        api = ApiFactory(app, db=db, url_prefix="/api")
        api.register_resource(UserResource, path="/users")
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
    ):
        self._target = target
        self._db = db
        self._url_prefix = ("/" + (url_prefix or "").strip("/")).rstrip("/") if url_prefix else ""
        self._docs = docs
        self._resources: List[type] = []
        self._openapi_spec = OpenAPISpec(docs=docs)
        if openapi_url is not None:
            openapi_rule = (
                f"{self._url_prefix}/{openapi_url.strip('/')}"
                if self._url_prefix
                else f"/{openapi_url.strip('/')}"
            )
            target.add_url_rule(
                openapi_rule,
                endpoint=f"openapi_{id(self)}",
                view_func=self._serve_openapi,
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

    def _serve_openapi(self):
        return jsonify(self._openapi_spec.to_dict()), 200

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

        Returns the resource class.
        """
        is_api_resource = isinstance(resource, type) and issubclass(resource, ApiResource)

        if is_api_resource and self._db is not None:
            resource.db = self._db

        ep = endpoint or resource.__name__.lower()

        defined = set(getattr(resource, "methods", None) or ())

        if urls is None:
            base = self._base_path(resource, path, params)
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

        blueprint_prefix = getattr(self._target, "url_prefix", None) or ""
        for index, (rule, verbs) in enumerate(rules):
            rule_endpoint = ep if index == 0 else f"{ep}_detail"
            options = dict(rule_options)
            if verbs and "methods" not in options:
                options["methods"] = verbs
            self._add_rule(rule, rule_endpoint, resource, options)
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
                        f":{getattr(resource, 'pk_field', 'id')}>" in rule
                        or f"<{getattr(resource, 'pk_field', 'id')}>" in rule
                    )
                ),
            )

        self._resources.append(resource)
        return resource

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

    def _add_rule(self, rule, endpoint, resource, options):
        self._target.add_url_rule(
            rule, endpoint=endpoint, view_func=resource.as_view(endpoint), **options
        )

    def _install_json_error_handler(self):
        def handler(exc: HTTPException):
            return {"message": exc.description or exc.name}, exc.code

        self._target.register_error_handler(HTTPException, handler)
