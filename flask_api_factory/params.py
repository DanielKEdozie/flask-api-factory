from __future__ import annotations

from flask_api_factory.errors import ApiError


class ParamMixin:
    """URL-parameter validation/resolution for an ``ApiResource``.

    Class-level attributes
    ----------------------
    route_params    Validation rules for URL params declared in ``params``.
                    Dict keyed by param name::

                        route_params = {
                            "fleet_id": {"type": int, "required": True}
                        }

                    ``type`` is used to coerce the value; ``required=True``
                    (default) returns 400 if the param is missing.

    resolve_params  Dict mapping param name → callable.  Each callable
                    receives the coerced param value and must return either
                    the resolved object, ``None`` (→ 404), or raise
                    :class:`ApiError`.  For backward compatibility a
                    ``(payload, status)`` tuple is also accepted and is
                    converted to an :class:`ApiError`.  The resolved objects
                    are stored in ``self._resolved`` and are also
                    auto-injected as FK values into ``post`` payloads and as
                    filters into ``_build_query``::

                        resolve_params = {
                            "fleet_id": lambda fid: db.session.get(Fleet, fid)
                        }
    """

    # {"fleet_id": {"type": int, "required": True, "default": None}}
    route_params: dict = {}
    # {"fleet_id": lambda fid: Fleet.query.get(fid) or ({"message": "Fleet not found"}, 404)}
    resolve_params: dict = {}

    # ---- param resolution ----------------------------------------------

    def _resolve_url_params(self, kwargs: dict):
        """Validate and resolve URL params declared in ``route_params`` and
        ``resolve_params``.

        Coerces each param value using the ``type`` rule, then calls the
        matching ``resolve_params`` callable (if any).  Raises
        :class:`ApiError` on failure.

        Resolved objects are stored in ``self._resolved`` keyed by param name.
        """
        self._resolved = {}

        for name, rules in (self.route_params or {}).items():
            required = rules.get("required", True)
            default = rules.get("default", None)
            coerce = rules.get("type", str)

            raw = kwargs.get(name, default)

            if raw is None:
                if required:
                    raise ApiError(400, f"URL parameter '{name}' is required")
                continue

            # Coerce
            try:
                value = coerce(raw)
            except (TypeError, ValueError):
                raise ApiError(400, f"Invalid value for URL parameter '{name}'") from None

            # Resolve
            resolver = (self.resolve_params or {}).get(name)
            if resolver is not None:
                result = resolver(value)
                # Backward compatibility: a (payload, status) tuple is
                # converted to an ApiError.
                if isinstance(result, tuple):
                    payload, status = result
                    message = payload.get("message", "Error") if isinstance(payload, dict) else str(payload)
                    errors = payload.get("errors") if isinstance(payload, dict) else None
                    raise ApiError(status, message, errors=errors)
                if result is None:
                    raise ApiError(404, f"'{name}' {coerce.__name__} {value} not found")
                self._resolved[name] = result
            else:
                # No resolver — just store the coerced value
                self._resolved[name] = value

    def _parent_filters(self) -> dict:
        """Return a dict of ``{model_col: value}`` derived from resolved params.

        For each resolved param named ``<model>_id`` (e.g. ``fleet_id``), if
        the current model has that column, it is included as a filter so
        ``_build_query`` scopes results to the parent automatically.
        """
        filters = {}
        for name, obj in self._resolved.items():
            col = getattr(self.model, name, None)
            if col is not None:
                # obj may be the resolved model instance or the raw coerced value
                value = getattr(obj, "id", obj) if hasattr(obj, "id") and name.endswith("_id") else obj
                filters[name] = value
        return filters
