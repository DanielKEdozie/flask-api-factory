from __future__ import annotations

from typing import List, Optional


def normalize_prefix(params) -> str:
    """Normalize a nesting ``params`` value into a URL prefix string.

    Accepted forms:

    - ``None``                  → ``""``
    - ``"/fleets/<int:fleet_id>"``   (plain string prefix)
    - ``("/fleets", "<int:fleet_id>")``      (a single ``(base, converter)`` tuple)
    - ``[("/fleets", "<int:fleet_id>"), ...]`` (a list of such tuples)

    Leading/trailing slashes on every segment are normalized, so
    ``("categories", "<int:category_id>")`` and ``("/categories/", "<int:category_id>/")``
    both yield ``"/categories/<int:category_id>"``.

    Raises ``ValueError`` for anything else — including flat lists of plain
    strings (``["categories", "<int:category_id>"]``), where positional
    pairing is implicit and mistakes would produce silently wrong URLs.
    """
    if params is None:
        return ""

    # Plain string prefix
    if isinstance(params, str):
        return ("/" + params.strip().strip("/")).rstrip("/") or ""

    # A bare single pair is accepted for convenience: ("/fleets", "<int:fleet_id>")
    if (isinstance(params, tuple) and len(params) == 2
            and all(isinstance(seg, str) for seg in params)):
        params = [params]

    if not isinstance(params, (list, tuple)):
        raise ValueError(
            "params must be a URL prefix string ('/fleets/<int:fleet_id>'), "
            "a single (base, converter) tuple, or a list of such tuples; "
            f"got {params!r}"
        )

    prefix = ""
    for entry in params:
        if not (isinstance(entry, tuple) and len(entry) == 2
                and all(isinstance(seg, str) for seg in entry)):
            raise ValueError(
                "params entries must be (base, converter) tuples like "
                "('fleets', '<int:fleet_id>'); got entry: "
                f"{entry!r} in {params!r}. "
                "For a flat URL prefix, pass a single string instead."
            )
        base, converter = entry
        base = base.strip("/")
        prefix += f"/{base}/{converter.strip('/')}" if base else f"/{converter.strip('/')}"
    return prefix


class RoutingMixin:
    """Routing configuration for an ``ApiResource``.

    The URL shape is **not** declared on the class — ``path``, ``endpoint``
    and ``params`` are all supplied at registration time via
    ``ApiFactory.register_resource``, so the same resource can be mounted at
    different URLs.

    Class-level attributes
    ----------------------
    pk_field    Primary-key attribute on the model (default ``"id"``).
    pk_type     URL converter for the PK segment (default ``"int"``).

    ``params`` (registration-time) is the URL prefix the resource is nested
    under. Two equivalent forms — see :func:`normalize_prefix`:

        params="/fleets/<int:fleet_id>"                # plain string
        params=[("/fleets", "<int:fleet_id>")]          # list of (base, converter) tuples
        # → /fleets/<int:fleet_id>/images
    """

    # Primary key
    pk_field: str = "id"
    pk_type: str = "int"

    # ---- routing helper ------------------------------------------------

    @classmethod
    def routes(cls, path: str, endpoint: Optional[str] = None,
               params=None) -> list[tuple]:
        """Return ``[(url, endpoint), ...]`` for registration ``path``.

        Builds URLs from ``params`` + ``path`` + optional PK detail segment.
        ``endpoint`` defaults to the lowercased class name.

        Example — flat resource::

            WidgetResource.routes("/widgets")
            # → [("/widgets", "widgetresource"),
            #    ("/widgets/<int:id>", "widgetresource_detail")]

        Example — nested resource (either ``params`` form)::

            ImageResource.routes("/images", params="/fleets/<int:fleet_id>")
            ImageResource.routes("/images",
                                 params=[("/fleets", "<int:fleet_id>")])
            # → [("/fleets/<int:fleet_id>/images", "imageresource"),
            #    ("/fleets/<int:fleet_id>/images/<int:id>", "imageresource_detail")]
        """
        if not path:
            raise ValueError(f"{cls.__name__}: 'path' is required to build routes")

        prefix = normalize_prefix(params)
        base = f"{prefix}/{path.strip('/')}"
        ep = endpoint or cls.__name__.lower()
        detail = f"{base}/<{cls.pk_type}:{cls.pk_field}>"
        return [(base, ep), (detail, f"{ep}_detail")]
