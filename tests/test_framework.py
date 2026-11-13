"""Framework-level tests for flask_api_factory.

These deliberately avoid the host application (``app/``) to prove the framework
stands on its own. Run with::

    python -m unittest discover -s tests
"""

import inspect
import unittest

from flask import Blueprint, Flask
from flask.views import MethodView
from flask_sqlalchemy import SQLAlchemy
from marshmallow import Schema, fields

from flask_api_factory import ApiFactory, ApiResource
from flask_api_factory import factory, pagination, params, querying, resource, routing, service


db = SQLAlchemy()


class Widget(db.Model):
    __tablename__ = "widgets"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    kind = db.Column(db.String(20), default="generic")


class WidgetSchema(Schema):
    id = fields.Int(dump_only=True)
    name = fields.Str(required=True)
    kind = fields.Str()


class WidgetResource(ApiResource):
    schema = WidgetSchema
    model = Widget
    filter_fields = ["kind"]
    paginate = False


class FrameworkTestCase(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite://"
        self.app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
        db.init_app(self.app)

        self.api = ApiFactory(
            self.app,
            db=db,
            url_prefix="/api",
            docs={
                "title": "Widget API",
                "version": "2.0.0",
                "description": "Widget management.",
                "contact": {"name": "Support", "email": "support@example.com"},
                "servers": ["https://api.example.test"],
            },
        )
        self.api.register_resource(WidgetResource, path="/widgets")

        with self.app.app_context():
            db.create_all()

        self.client = self.app.test_client()

    def tearDown(self):
        with self.app.app_context():
            db.session.remove()
            db.drop_all()

    def test_create_and_retrieve(self):
        created = self.client.post("/api/widgets", json={"name": "hammer", "kind": "tool"})
        self.assertEqual(created.status_code, 201)
        payload = created.get_json()
        self.assertEqual(payload["data"]["name"], "hammer")

        got = self.client.get(f"/api/widgets/{payload['data']['id']}")
        self.assertEqual(got.status_code, 200)
        self.assertEqual(got.get_json()["data"]["name"], "hammer")

    def test_list_and_filter(self):
        self.client.post("/api/widgets", json={"name": "a", "kind": "tool"})
        self.client.post("/api/widgets", json={"name": "b", "kind": "toy"})

        all_items = self.client.get("/api/widgets").get_json()["data"]
        self.assertEqual(sorted(w["name"] for w in all_items), ["a", "b"])

        filtered = self.client.get("/api/widgets?kind=tool").get_json()["data"]
        self.assertEqual([w["name"] for w in filtered], ["a"])

    def test_validation_error_is_json_422(self):
        resp = self.client.post("/api/widgets", json={"kind": "tool"})
        self.assertEqual(resp.status_code, 422)
        self.assertIn("errors", resp.get_json())

    def test_update_and_delete(self):
        wid = self.client.post("/api/widgets", json={"name": "a"}).get_json()["data"]["id"]

        updated = self.client.put(f"/api/widgets/{wid}", json={"name": "z"})
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.get_json()["data"]["name"], "z")

        deleted = self.client.delete(f"/api/widgets/{wid}")
        self.assertEqual(deleted.status_code, 204)
        self.assertEqual(self.client.get(f"/api/widgets/{wid}").status_code, 404)

    def test_detail_route_rejects_post(self):
        wid = self.client.post("/api/widgets", json={"name": "a"}).get_json()["data"]["id"]

        resp = self.client.post(f"/api/widgets/{wid}", json={"name": "b"})

        self.assertEqual(resp.status_code, 405)

    def test_collection_route_rejects_put_and_delete(self):
        self.assertEqual(self.client.put("/api/widgets", json={"name": "b"}).status_code, 405)
        self.assertEqual(self.client.delete("/api/widgets").status_code, 405)

    def test_openapi_endpoint_describes_registered_resources(self):
        response = self.client.get("/api/openapi.json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "application/json")
        spec = response.get_json()
        self.assertEqual(spec["openapi"], "3.0.3")
        self.assertEqual(spec["info"]["title"], "Widget API")
        self.assertEqual(spec["servers"], [{"url": "https://api.example.test"}])
        self.assertNotIn("servers", spec["info"])

        collection = spec["paths"]["/api/widgets"]
        self.assertEqual(set(collection), {"get", "post"})
        self.assertIn("kind", [p["name"] for p in collection["get"]["parameters"]])
        self.assertIn(
            "oneOf",
            collection["get"]["responses"]["200"]["content"]["application/json"]
            ["schema"]["properties"]["data"],
        )
        self.assertEqual(
            collection["post"]["requestBody"]["content"]["application/json"]
            ["schema"]["$ref"],
            "#/components/schemas/WidgetSchema",
        )

        detail = spec["paths"]["/api/widgets/{id}"]
        self.assertEqual(set(detail), {"get", "put", "patch", "delete"})
        self.assertEqual(detail["get"]["parameters"][0]["schema"], {
            "type": "integer",
            "format": "int32",
        })
        self.assertIn("WidgetSchema", spec["components"]["schemas"])

    def test_openapi_endpoint_can_be_disabled(self):
        app = Flask(__name__)
        api = ApiFactory(app, openapi_url=None)

        self.assertFalse(any(rule.rule == "/openapi.json" for rule in app.url_map.iter_rules()))
        self.assertEqual(api.openapi_spec.to_dict()["paths"], {})

    def test_openapi_paths_include_blueprint_prefix(self):
        app = Flask(__name__)
        blueprint = Blueprint("versioned_api", __name__, url_prefix="/v1")

        class HealthResource(MethodView):
            def get(self):
                return {"status": "ok"}

        api = ApiFactory(blueprint)
        api.register_resource(HealthResource, path="/health")
        app.register_blueprint(blueprint)

        client = app.test_client()
        self.assertEqual(client.get("/v1/openapi.json").status_code, 200)
        self.assertIn("/v1/health", client.get("/v1/openapi.json").get_json()["paths"])

    def test_unbound_resource_raises(self):
        class UnboundResource(ApiResource):
            schema = WidgetSchema
            model = Widget

        with self.assertRaises(RuntimeError):
            UnboundResource()

    def test_framework_does_not_depend_on_flask_restful(self):
        modules = [factory, pagination, params, querying, resource, routing, service]
        for module in modules:
            self.assertNotIn("flask_restful", inspect.getsource(module))


if __name__ == "__main__":
    unittest.main()
