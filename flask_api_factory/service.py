from __future__ import annotations

from flask_sqlalchemy import SQLAlchemy
from marshmallow import Schema, ValidationError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from flask_api_factory.errors import ApiError


class ApiService:
    """Lightweight CRUD service for a SQLAlchemy model + Marshmallow schema.

    Success payloads are wrapped as ``{"data": ...}`` and returned as
    ``(payload, status_code)`` tuples. Errors raise :class:`ApiError`,
    which the factory renders as a JSON envelope.
    """

    def __init__(self, db: SQLAlchemy, schema: Schema, model=None,
                 dump_schema: Schema = None, hooks: dict = None):
        self.db = db
        self.schema = schema
        self.dump_schema = dump_schema or schema
        self.model = model
        self.hooks = hooks or {}

    # ---- serialization -------------------------------------------------

    def load(self, data, partial=False):
        try:
            return self.schema.load(data, partial=partial)
        except ValidationError as e:
            raise ApiError(422, "Invalid data", errors=e.messages) from e

    def dump(self, data, many=False):
        return self.dump_schema.dump(data, many=many)

    # ---- responses -----------------------------------------------------

    def _envelope_response(self, data):
        return {"data": data}

    def response(self, data, code=200):
        if isinstance(data, tuple):
            data, code = data
        return self._envelope_response(data), code

    def error(self, code, message, errors=None):
        """Raise an :class:`ApiError`. Kept for backward compatibility."""
        raise ApiError(code, message, errors=errors)

    # ---- persistence ---------------------------------------------------

    def commit(self):
        try:
            self.db.session.commit()
        except IntegrityError as e:
            self.db.session.rollback()
            detail = str(e.orig) if getattr(e, "orig", None) else str(e)
            raise ApiError(409, "Conflict", errors={"detail": detail}) from e
        except SQLAlchemyError as e:
            self.db.session.rollback()
            raise ApiError(500, "Database error", errors={"detail": str(e)}) from e

    def _get_obj(self, pk, pk_field='id', query=None):
        query = query if query is not None else self.db.session.query(self.model)
        obj = query.filter(getattr(self.model, pk_field) == pk).first()
        if not obj:
            raise ApiError(404, f"{self.model.__name__} not found")
        return obj

    # ---- hooks ---------------------------------------------------------

    def _execute_hook(self, name, *args, **kwargs):
        """Run a lifecycle hook.

        Resolution order: an entry in ``self.hooks`` keyed by hook name, then
        a same-named method override on the service. Missing hooks are no-ops.
        """
        hook = (self.hooks or {}).get(name) or getattr(self, name, None)
        if hook is None:
            return None
        return hook(*args, **kwargs)

    def before_load(self, payload, instance=None, mode="create"):
        """Called before marshmallow load; override to mutate ``payload`` in place."""

    def after_load(self, instance, data, mode="create"):
        """Called after marshmallow load; override to react to loaded data."""

    def before_commit(self, instance, data, mode="create"):
        """Called after load and before commit; override to mutate the instance
        (e.g. hash a password) once it has been built/updated."""

    # ---- CRUD ----------------------------------------------------------

    def retrieve(self, pk, query=None, pk_field='id'):
        if query is not None:
            obj = query.filter(getattr(self.model, pk_field) == pk).first()
            if not obj:
                raise ApiError(404, f"{self.model.__name__} not found")
        else:
            obj = self._get_obj(pk, pk_field)
        return self.response(self.dump(obj))

    def list_all(self, query=None, paginate=False, page=1, per_page=10):
        query = query if query is not None else self.db.session.query(self.model)
        if paginate:
            paginated = query.paginate(page=page, per_page=per_page, error_out=False)
            return self.response({
                "items": self.dump(paginated.items, many=True),
                "total": paginated.total,
                "pages": paginated.pages,
                "page": paginated.page,
                "per_page": paginated.per_page,
            })
        return self.response(self.dump(query.all(), many=True))

    def create(self, data):
        self._execute_hook("before_load", data, mode="create")
        loaded_data = self.load(data)
        self._execute_hook("after_load", None, loaded_data, mode="create")
        obj = self.model(**loaded_data)
        self._execute_hook("before_commit", obj, loaded_data, mode="create")
        self.db.session.add(obj)
        self.commit()
        return self.response(self.dump(obj), 201)

    def update(self, pk, data, pk_field='id', partial=False, query=None):
        obj = self._get_obj(pk, pk_field, query=query)
        self._execute_hook("before_load", data, instance=obj, mode="update")
        loaded_data = self.load(data, partial=partial)
        self._execute_hook("after_load", obj, loaded_data, mode="update")
        for key, value in loaded_data.items():
            setattr(obj, key, value)
        self._execute_hook("before_commit", obj, loaded_data, mode="update")
        self.commit()
        return self.response(self.dump(obj))

    def delete(self, pk, pk_field='id', query=None):
        obj = self._get_obj(pk, pk_field, query=query)
        self.db.session.delete(obj)
        self.commit()
        return self.response(None, 204)
