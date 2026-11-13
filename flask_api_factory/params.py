from __future__ import annotations


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
                    the resolved object or a ``(payload, status)`` error
                    tuple.  The resolved objects are stored in
                    ``self._resolved`` and are also auto-injected as FK
                    values into ``post`` payloads and as filters into
                    ``_build_query``::

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
        matching ``resolve_params`` callable (if any).  Returns ``None`` on
        success; returns an error ``(payload, status)`` tuple on failure so
        callers can short-circuit immediately.

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
                    return {"message": f"URL parameter '{name}' is required"}, 400
                continue

            # Coerce
            try:
                value = coerce(raw)
            except (TypeError, ValueError):
                return {"message": f"Invalid value for URL parameter '{name}'"}, 400

            # Resolve
            resolver = (self.resolve_params or {}).get(name)
            if resolver is not None:
                result = resolver(value)
                # Resolver may return an error tuple
                if isinstance(result, tuple):
                    return result
                if result is None:
                    return {"message": f"'{name}' {coerce.__name__} {value} not found"}, 404
                self._resolved[name] = result
            else:
                # No resolver — just store the coerced value
                self._resolved[name] = value

        return None  # success

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
