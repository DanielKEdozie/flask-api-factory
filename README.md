# flask-api-factory

Reusable, Flask-native CRUD API building blocks. No `flask-restful` dependency.

## Install

```bash
pip install -e .
```

## Quick start

```python
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_api_factory import ApiFactory, ApiResource

db = SQLAlchemy()
app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///app.db"
db.init_app(app)


class Widget(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)


class WidgetSchema(Schema):
    id = fields.Int(dump_only=True)
    name = fields.Str(required=True)


class WidgetResource(ApiResource):
    schema = WidgetSchema
    model = Widget


api = ApiFactory(app, db=db, url_prefix="/api")
api.register_resource(WidgetResource, path="/widgets")
```

This exposes:

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/api/widgets` | list (filter/sort/paginate) |
| `POST` | `/api/widgets` | create |
| `GET` | `/api/widgets/<id>` | retrieve |
| `PUT`/`PATCH` | `/api/widgets/<id>` | update |
| `DELETE` | `/api/widgets/<id>` | delete |

## OpenAPI

A generated OpenAPI 3.0.3 document is served at `/openapi.json` (relative to the
factory's target, so `/api/openapi.json` in the example above).

```python
api = ApiFactory(
    app,
    db=db,
    url_prefix="/api",
    docs={
        "title": "Widget API",
        "version": "1.0.0",
        "description": "Widget management.",
        "contact": {"name": "Support", "email": "support@example.com"},
        "servers": [{"url": "/api"}],
    },
)
```

The document is built from the routes you register: paths, verbs, path/query
parameters, and Marshmallow-derived component schemas. Pass `openapi_url=None`
to disable the endpoint, or `openapi_url="/docs/openapi.json"` to move it.

## Resource features

- **Filtering** — `filter_fields` collected from the query string.
- **Sorting** — `sort_fields`, `default_sort_field`, `default_sort_order`.
- **Eager loading** — `eager_loads` relationship names.
- **Pagination** — `paginate`, `page_size`, `max_page_size`, `?paginate=`.
- **Nesting** — `params=[("/fleets", "<int:fleet_id>")]` at registration time.
- **Hooks** — `before_load`, `after_load`, `before_commit`.

## Tests

```bash
python -m unittest discover -s tests
```