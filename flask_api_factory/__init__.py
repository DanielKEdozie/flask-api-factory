"""flask-api-factory

Reusable, Flask-native CRUD API building blocks. No flask-restful dependency.

    from flask_api_factory import ApiFactory, ApiResource, ApiService

- ``ApiService``    framework-light CRUD service (SQLAlchemy + Marshmallow).
- ``ApiResource``   Flask ``MethodView`` that wires an ``ApiService`` over HTTP.
                    Its functionality is split across focused mixins:
                    ``RoutingMixin`` (routing/routes), ``ParamMixin`` (URL
                    param resolution), ``QueryingMixin`` (filters/sort/eager
                    loads), ``PaginationMixin`` (pagination).
- ``ApiFactory``    registers resources onto a Flask app or Blueprint, binding
                    the ``SQLAlchemy`` instance each resource needs.
"""

from flask_api_factory.service import ApiService
from flask_api_factory.routing import RoutingMixin
from flask_api_factory.params import ParamMixin
from flask_api_factory.querying import QueryingMixin
from flask_api_factory.pagination import PaginationMixin
from flask_api_factory.resource import ApiResource
from flask_api_factory.factory import ApiFactory, ResourceRegistration
from flask_api_factory.errors import ApiError, abort

__version__ = "0.1.0"

__all__ = [
    "ApiService",
    "ApiResource",
    "ApiFactory",
    "ResourceRegistration",
    "ApiError",
    "abort",
    "RoutingMixin",
    "ParamMixin",
    "QueryingMixin",
    "PaginationMixin",
]
