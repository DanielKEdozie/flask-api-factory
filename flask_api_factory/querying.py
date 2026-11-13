from __future__ import annotations

from typing import List, Optional

from flask import request


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
    sort_fields         Model attribute names sortable via ``?sort=``.
    default_sort_field  Column to sort by when none is specified.
    default_sort_order  ``"asc"`` or ``"desc"``.
    eager_loads         Relationship names to joinedload on every query.
    """

    # Filtering / sorting
    filter_fields: List[str] = []
    sort_fields: List[str] = []
    default_sort_field: Optional[str] = None
    default_sort_order: str = "asc"  # "asc" | "desc"

    # Eager loading
    eager_loads: List[str] = []

    # ---- filtering -----------------------------------------------------

    def _collect_filters(self) -> dict:
        """Gather the filters to apply to a list query.

        Merges parent-scoping filters (``_parent_filters``) with any
        ``filter_fields`` present in the query string.  Parent filters win,
        so a client cannot override nested-route scoping via query args.
        """
        filters = dict(self._parent_filters())
        for field in (self.filter_fields or []):
            value = request.args.get(field)
            if value is not None:
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

        # Eager loads
        for rel_name in (self.eager_loads or []):
            attr = getattr(self.model, rel_name, None)
            if attr is not None:
                query = query.options(joinedload(attr))

        # Filtering: parent scoping + query-string ``filter_fields``, both
        # routed through the overridable ``_apply_filters`` hook.
        query = self._apply_filters(
            query, self._collect_filters(), self.filter_context()
        )

        # Sorting
        sort_param = request.args.get("sort", self.default_sort_field)
        order_param = request.args.get("order", self.default_sort_order).lower()
        allowed_sorts = set(self.sort_fields or [])
        if sort_param and (sort_param in allowed_sorts or not allowed_sorts):
            col = getattr(self.model, sort_param, None)
            if col is not None:
                query = query.order_by(col.desc() if order_param == "desc" else col.asc())

        return query
