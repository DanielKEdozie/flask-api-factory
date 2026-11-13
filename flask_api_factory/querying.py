from __future__ import annotations

from datetime import date, datetime
from typing import List, Optional

from flask import request


class FilterError(Exception):
    """Raised when a query-string filter value cannot be coerced."""


_TRUE_VALUES = {"true", "1", "yes", "on"}
_FALSE_VALUES = {"false", "0", "no", "off"}


class QueryingMixin:
    """List-query construction for an ``ApiResource``: parent scoping,
    query-string filtering, sorting, and eager loading.

    Class-level attributes
    ----------------------
    filter_fields       Model attribute names filterable via query-string.
                        Filters are collected by ``_collect_filters`` and applied
                        by the overridable ``_apply_filters(query, filters, ctx)``
                        hook (default: equality match). Override that method to
                        add operators (ranges, ``like``, ``in``) or joins.
    filter_types        Optional ``{field: type}`` mapping to override how a
                        query-string value is coerced. ``type`` may be a Python
                        type (``int``, ``float``, ``bool``, ``str``, ``date``,
                        ``datetime``) or a callable. Fields not listed fall back
                        to the column's Python type.
    sort_fields         Model attribute names sortable via ``?sort=``.
    default_sort_field  Column to sort by when none is specified.
    default_sort_order  ``"asc"`` or ``"desc"``.
    eager_loads         Relationship names (``"vendor"``) or SQLAlchemy loader
                        options (``joinedload(Fleet.machine_type)``) to apply on
                        every query.
    """

    # Filtering / sorting
    filter_fields: List[str] = []
    filter_types: dict = {}
    sort_fields: List[str] = []
    default_sort_field: Optional[str] = None
    default_sort_order: str = "asc"  # "asc" | "desc"

    # Eager loading
    eager_loads: List = []

    # ---- filtering -----------------------------------------------------

    @staticmethod
    def _coerce_with_type(target_type, raw):
        """Coerce ``raw`` using an explicit ``filter_types`` entry."""
        try:
            if target_type is bool:
                lowered = str(raw).strip().lower()
                if lowered in _TRUE_VALUES:
                    return True
                if lowered in _FALSE_VALUES:
                    return False
                raise ValueError(f"expected a boolean, got {raw!r}")
            if target_type is datetime:
                return datetime.fromisoformat(str(raw))
            if target_type is date:
                return date.fromisoformat(str(raw))
            return target_type(raw)
        except (TypeError, ValueError) as exc:
            raise FilterError(f"Invalid value for filter: {exc}") from exc

    @staticmethod
    def _coerce_filter_value(col, raw):
        """Coerce a query-string value to the column's Python type.

        Raises ``FilterError`` when the value cannot be converted.
        """
        python_type = getattr(getattr(col, "type", None), "python_type", None)
        if python_type is None:
            return raw
        return QueryingMixin._coerce_with_type(python_type, raw)

    def _collect_filters(self) -> dict:
        """Gather the filters to apply to a list query.

        Merges parent-scoping filters (``_parent_filters``) with any
        ``filter_fields`` present in the query string.  Parent filters win,
        so a client cannot override nested-route scoping via query args.

        Query-string values are coerced to the column's Python type; a value
        that cannot be converted raises ``FilterError``.
        """
        filters = dict(self._parent_filters())
        filter_types = self.filter_types or {}
        for field in (self.filter_fields or []):
            value = request.args.get(field)
            if value is None:
                continue
            if field in filter_types:
                value = self._coerce_with_type(filter_types[field], value)
            else:
                col = getattr(self.model, field, None)
                if col is not None:
                    value = self._coerce_filter_value(col, value)
            filters.setdefault(field, value)
        return filters

    def filter_context(self) -> dict:
        """Context handed to ``_apply_filters``. Override to add data."""
        return {
            "request": request,
            "resolved": self._resolved,
            "model": self.model,
        }

    def _apply_filters(self, query, filters, ctx):
        """Apply ``filters`` (``{model_attr: value}``) to ``query``.

        Default behaviour is an equality match per entry.  Override to
        support richer operators (ranges, ``like``, ``in``), joins, or to
        reinterpret values — ``ctx`` carries request context
        (``request`` / ``resolved`` / ``model``) so overrides need not reach
        for globals.

        Note: ``filters`` also contains parent-scoping entries, so overrides
        should keep applying them to preserve nested-route isolation.
        """
        for field, value in filters.items():
            col = getattr(self.model, field, None)
            if col is not None:
                query = query.filter(col == value)
        return query

    # ---- internal helpers ----------------------------------------------

    def _build_query(self):
        """Return a base query with parent filters, eager-loads, filters, and
        sorting applied."""
        from sqlalchemy.orm import joinedload

        query = self.db.session.query(self.model)

        # Eager loads: accept relationship-name strings or SQLAlchemy
        # loader options (e.g. joinedload(Fleet.machine_type)).
        for entry in (self.eager_loads or []):
            if isinstance(entry, str):
                attr = getattr(self.model, entry, None)
                if attr is not None:
                    query = query.options(joinedload(attr))
            else:
                # Assume a pre-built loader option (joinedload/selectinload/...)
                query = query.options(entry)

        # Filtering: parent scoping + query-string ``filter_fields``, both
        # routed through the overridable ``_apply_filters`` hook.
        query = self._apply_filters(
            query, self._collect_filters(), self.filter_context()
        )

        # Sorting: client-supplied ``?sort=`` must be in ``sort_fields``.
        # A developer-set ``default_sort_field`` is always allowed.
        sort_param = request.args.get("sort", self.default_sort_field)
        order_param = request.args.get("order", self.default_sort_order).lower()
        allowed_sorts = set(self.sort_fields or [])
        if sort_param and (
            sort_param in allowed_sorts or sort_param == self.default_sort_field
        ):
            col = getattr(self.model, sort_param, None)
            if col is not None:
                query = query.order_by(col.desc() if order_param == "desc" else col.asc())

        return query
