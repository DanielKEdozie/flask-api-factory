from __future__ import annotations

from flask import request


class PaginationMixin:
    """Pagination behaviour for an ``ApiResource`` list responses.

    Class-level attributes
    ----------------------
    paginate      Whether list responses are paginated by default (``True``).
                  Override per-request with ``?paginate=true|false``.
    page_size     Default page size (default ``10``).
    max_page_size Hard ceiling on page size (default ``100``).
    """

    # Pagination
    paginate: bool = True
    page_size: int = 10
    max_page_size: int = 100

    # ---- helpers -------------------------------------------------------

    def _pagination_params(self):
        try:
            page = max(1, int(request.args.get("page", 1)))
        except (TypeError, ValueError):
            page = 1
        try:
            per_page = min(
                max(1, int(request.args.get("per_page", self.page_size))),
                self.max_page_size,
            )
        except (TypeError, ValueError):
            per_page = self.page_size
        return page, per_page

    def _should_paginate(self) -> bool:
        """Determine if the list response should be paginated.

        Checks the ``?paginate=`` query param first; if present, it overrides
        the class-level ``paginate`` attribute. Accepted values are
        ``true``, ``false``, ``1``, ``0``, ``yes``, ``no`` (case-insensitive).
        """
        paginate_param = request.args.get("paginate", "").strip().lower()
        if paginate_param in ("true", "1", "yes"):
            return True
        if paginate_param in ("false", "0", "no"):
            return False
        return self.paginate
