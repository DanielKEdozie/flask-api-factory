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

from flask_api_factory import (
    ApiError,
    ApiFactory,
    ApiResource,
    ResourceRegistration,
    abort,
)
from flask_api_factory import factory, pagination, params, querying, resource, routing, service


db = SQLAlchemy()


class Widget(db.Model):
    __tablename__ = "widgets"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False)
    kind = db.Column(db.String(20), default="generic")
    count = db.Column(db.Integer, default=0)
    parent_id = db.Column(db.Integer, db.ForeignKey("widgets.id"), nullable=True)

    parent = db.relationship("Widget", remote_side=[id], backref="children")


class Profile(db.Model):
    __tablename__ = "profiles"

    id = db.Column(db.Integer, primary_key=True)
    widget_id = db.Column(db.Integer, db.ForeignKey("widgets.id"), unique=True, nullable=False)
    bio = db.Column(db.String(100), nullable=False)


class ProfileNote(db.Model):
    __tablename__ = "profile_notes"

    id = db.Column(db.Integer, primary_key=True)
    profile_id = db.Column(db.Integer, db.ForeignKey("profiles.id"), nullable=False)
    text = db.Column(db.String(100), nullable=False)


class WidgetSchema(Schema):
    id = fields.Int(dump_only=True)
    name = fields.Str(required=True)
    kind = fields.Str()
    count = fields.Int()
    parent_id = fields.Int()


class WidgetResource(ApiResource):
    schema = WidgetSchema
    model = Widget
    filter_fields = ["kind", "count"]
    sort_fields = ["name"]
    paginate = False


class ProfileSchema(Schema):
    id = fields.Int(dump_only=True)
    widget_id = fields.Int()
    bio = fields.Str(required=True)


class ProfileResource(ApiResource):
    schema = ProfileSchema
    model = Profile


class ClassProfileResource(ProfileResource):
    parent_resource = WidgetResource
    parent_fk = "widget_id"
    singleton = True


class LegacyClassProfileResource(ProfileResource):
    parent_resource = WidgetResource
    parent_fk = "widget_id"
    one_to_one = True


class ProfileNoteSchema(Schema):
    id = fields.Int(dump_only=True)
    profile_id = fields.Int()
    text = fields.Str(required=True)


class ProfileNoteResource(ApiResource):
    schema = ProfileNoteSchema
    model = ProfileNote


class ReadOnlyWidgetResource(WidgetResource):
    allow_create = False
    allow_update = False
    allow_delete = False


class TypedFilterWidgetResource(WidgetResource):
    # Transform the raw query-string value to prove filter_types is applied
    # instead of the column's Python type.
    filter_types = {"count": lambda raw: int(raw) + 100}


class EagerLoadWidgetResource(WidgetResource):
    from sqlalchemy.orm import joinedload as _joinedload
    eager_loads = [_joinedload(Widget.parent)]


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
        self.widgets = self.api.register_resource(WidgetResource, path="/widgets")

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

    def test_registration_handle_supports_nested_and_chained_resources(self):
        children = self.widgets.nested_resource(
            WidgetResource,
            path="children",
            fk="parent_id",
            param_name="widget_id",
        )
        self.assertIsInstance(children, ResourceRegistration)
        self.assertIs(children.resource.__bases__[0], WidgetResource)
        grandchildren = children.nested_resource(
            WidgetResource,
            path="grandchildren",
            fk="parent_id",
            param_name="child_id",
        )
        self.assertTrue(grandchildren.path.endswith("/grandchildren"))

        root_id = self.client.post(
            "/api/widgets", json={"name": "root"}
        ).get_json()["data"]["id"]
        other_root_id = self.client.post(
            "/api/widgets", json={"name": "other-root"}
        ).get_json()["data"]["id"]

        child = self.client.post(
            f"/api/widgets/{root_id}/children",
            json={"name": "child", "parent_id": other_root_id},
        )
        self.assertEqual(child.status_code, 201)
        child_data = child.get_json()["data"]
        self.assertEqual(child_data["parent_id"], root_id)

        wrong_parent_child_url = (
            f"/api/widgets/{other_root_id}/children/{child_data['id']}"
        )
        self.assertEqual(
            self.client.patch(wrong_parent_child_url, json={"name": "hijacked"})
            .status_code,
            404,
        )
        self.assertEqual(
            self.client.put(
                wrong_parent_child_url,
                json={"name": "hijacked"},
            ).status_code,
            404,
        )
        self.assertEqual(self.client.delete(wrong_parent_child_url).status_code, 404)
        updated_child = self.client.put(
            f"/api/widgets/{root_id}/children/{child_data['id']}",
            json={"name": "child", "parent_id": other_root_id},
        )
        self.assertEqual(updated_child.status_code, 200)
        self.assertEqual(updated_child.get_json()["data"]["parent_id"], root_id)
        self.assertEqual(
            self.client.get(f"/api/widgets/{root_id}/children/{child_data['id']}")
            .get_json()["data"]["name"],
            "child",
        )
        self.assertEqual(
            self.client.get(f"/api/widgets/{other_root_id}/children")
            .get_json()["data"],
            [],
        )

        wrong_parent = self.client.get(
            f"/api/widgets/{other_root_id}/children/{child_data['id']}/grandchildren"
        )
        self.assertEqual(wrong_parent.status_code, 404)

        created_grandchild = self.client.post(
            f"/api/widgets/{root_id}/children/{child_data['id']}/grandchildren",
            json={"name": "grandchild"},
        )
        self.assertEqual(created_grandchild.status_code, 201)
        self.assertEqual(created_grandchild.get_json()["data"]["parent_id"], child_data["id"])

    def test_factory_and_class_level_singleton_registration(self):
        profile_api = self.api.nested_resource(
            ProfileResource,
            parent=self.widgets,
            path="profile",
            defaults={"widget_id": "id"},
            singleton=True,
        )
        legacy_profile_api = self.widgets.nested_resource(
            ProfileResource,
            path="legacy-profile",
            fk="widget_id",
            one_to_one=True,
        )
        profile_api.nested_resource(
            ProfileNoteResource, path="notes", fk="profile_id"
        )
        self.api.register_resource(ClassProfileResource, path="class-profile")
        legacy_class_profile_api = self.api.register_resource(
            LegacyClassProfileResource, path="legacy-class-profile"
        )
        self.assertTrue(legacy_profile_api.singleton)
        self.assertTrue(legacy_profile_api.one_to_one)
        self.assertTrue(legacy_class_profile_api.singleton)

        widget = self.client.post(
            "/api/widgets", json={"name": "profile-owner"}
        ).get_json()["data"]
        profile_url = f"/api/widgets/{widget['id']}/profile"
        legacy_profile_url = f"/api/widgets/{widget['id']}/legacy-profile"

        self.assertEqual(self.client.get(profile_url).status_code, 404)
        created = self.client.post(
            profile_url,
            json={"bio": "A profile", "widget_id": widget["id"] + 100},
        )
        self.assertEqual(created.status_code, 201)
        profile = created.get_json()["data"]
        self.assertEqual(profile["widget_id"], widget["id"])
        self.assertEqual(self.client.get(profile_url).get_json()["data"]["bio"], "A profile")
        self.assertEqual(
            self.client.get(legacy_profile_url).get_json()["data"]["bio"],
            "A profile",
        )
        spec = self.client.get("/api/openapi.json").get_json()
        self.assertEqual(
            set(spec["paths"]["/api/widgets/{widget_id}/profile"]),
            {"get", "post", "put", "patch", "delete"},
        )
        self.assertEqual(
            spec["paths"]["/api/widgets/{widget_id}/profile"]["get"]
            ["responses"]["200"]["content"]["application/json"]["schema"]
            ["properties"]["data"]["$ref"],
            "#/components/schemas/ProfileSchema",
        )
        self.assertNotIn(
            "/api/widgets/{widget_id}/profile/{id}", spec["paths"]
        )

        updated = self.client.patch(profile_url, json={"bio": "Updated"})
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.get_json()["data"]["bio"], "Updated")
        replaced = self.client.put(profile_url, json={"bio": "Replaced"})
        self.assertEqual(replaced.status_code, 200)
        self.assertEqual(replaced.get_json()["data"]["bio"], "Replaced")

        note_url = f"{profile_url}/notes"
        note = self.client.post(
            note_url,
            json={"text": "Nested note", "profile_id": profile["id"] + 100},
        )
        self.assertEqual(note.status_code, 201)
        self.assertEqual(note.get_json()["data"]["profile_id"], profile["id"])
        self.assertEqual(
            self.client.get(note_url).get_json()["data"]["items"][0]["text"],
            "Nested note",
        )

        class_profile_url = f"/api/widgets/{widget['id']}/class-profile"
        self.assertEqual(
            self.client.get(class_profile_url).get_json()["data"]["bio"],
            "Replaced",
        )
        self.assertEqual(self.client.delete(profile_url).status_code, 204)
        self.assertEqual(self.client.get(class_profile_url).status_code, 404)

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

    def test_patch_allows_partial_update(self):
        wid = self.client.post("/api/widgets", json={"name": "a", "kind": "tool"}).get_json()["data"]["id"]

        resp = self.client.patch(f"/api/widgets/{wid}", json={"kind": "toy"})

        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["data"]["kind"], "toy")
        self.assertEqual(resp.get_json()["data"]["name"], "a")

    def test_put_requires_full_payload(self):
        wid = self.client.post("/api/widgets", json={"name": "a", "kind": "tool"}).get_json()["data"]["id"]

        resp = self.client.put(f"/api/widgets/{wid}", json={"kind": "toy"})

        self.assertEqual(resp.status_code, 422)

    def test_allow_flags_are_enforced(self):
        app = Flask(__name__)
        app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite://"
        app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
        db.init_app(app)
        api = ApiFactory(app, db=db, url_prefix="/api")
        api.register_resource(ReadOnlyWidgetResource, path="/readonly")
        with app.app_context():
            db.create_all()
        client = app.test_client()

        self.assertEqual(client.post("/api/readonly", json={"name": "a"}).status_code, 405)
        self.assertEqual(client.put("/api/readonly/1", json={"name": "a"}).status_code, 405)
        self.assertEqual(client.patch("/api/readonly/1", json={"name": "a"}).status_code, 405)
        self.assertEqual(client.delete("/api/readonly/1").status_code, 405)

    def test_invalid_filter_value_returns_400(self):
        self.client.post("/api/widgets", json={"name": "a", "kind": "tool", "count": 3})

        ok = self.client.get("/api/widgets?count=3")
        self.assertEqual(ok.status_code, 200)
        self.assertEqual([w["name"] for w in ok.get_json()["data"]], ["a"])

        bad = self.client.get("/api/widgets?count=notanint")
        self.assertEqual(bad.status_code, 400)
        self.assertIn("message", bad.get_json())

    def test_sort_rejects_unlisted_field(self):
        self.client.post("/api/widgets", json={"name": "a", "kind": "tool"})

        resp = self.client.get("/api/widgets?sort=kind")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual([w["name"] for w in resp.get_json()["data"]], ["a"])

    def test_openapi_includes_security_schemes(self):
        app = Flask(__name__)
        app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite://"
        app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
        db.init_app(app)
        api = ApiFactory(
            app,
            db=db,
            url_prefix="/api",
            docs={
                "title": "Secured API",
                "version": "1.0.0",
                "security_schemes": {
                    "bearerAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}
                },
                "security": [{"bearerAuth": []}],
            },
        )
        api.register_resource(WidgetResource, path="/widgets")
        with app.app_context():
            db.create_all()
        client = app.test_client()

        spec = client.get("/api/openapi.json").get_json()
        self.assertEqual(
            spec["components"]["securitySchemes"]["bearerAuth"]["scheme"],
            "bearer",
        )
        self.assertEqual(spec["security"], [{"bearerAuth": []}])

    def test_docs_method_configures_spec_and_ui(self):
        app = Flask(__name__)
        app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite://"
        app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
        db.init_app(app)
        api = ApiFactory(app, db=db, url_prefix="/api")
        api.docs(
            title="Chained API",
            version="3.0.0",
            description="Configured via docs().",
            ui="redoc",
            security_schemes={"bearerAuth": {"type": "http", "scheme": "bearer"}},
            security=[{"bearerAuth": []}],
        )
        api.register_resource(WidgetResource, path="/widgets")
        with app.app_context():
            db.create_all()
        client = app.test_client()

        spec = client.get("/api/openapi.json").get_json()
        self.assertEqual(spec["info"]["title"], "Chained API")
        self.assertEqual(spec["info"]["version"], "3.0.0")
        self.assertEqual(spec["components"]["securitySchemes"]["bearerAuth"]["scheme"], "bearer")

        page = client.get("/api/docs")
        self.assertEqual(page.status_code, 200)
        self.assertIn("text/html", page.mimetype)
        self.assertIn("redoc", page.get_data(as_text=True).lower())

    def test_docs_method_rejects_invalid_ui(self):
        app = Flask(__name__)
        api = ApiFactory(app)
        with self.assertRaises(ValueError):
            api.docs(ui="not-a-ui")

    def test_docs_page_defaults_to_swagger(self):
        app = Flask(__name__)
        app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite://"
        app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
        db.init_app(app)
        api = ApiFactory(app, db=db, url_prefix="/api")
        api.register_resource(WidgetResource, path="/widgets")
        with app.app_context():
            db.create_all()
        client = app.test_client()

        page = client.get("/api/docs")
        self.assertEqual(page.status_code, 200)
        self.assertIn("swagger", page.get_data(as_text=True).lower())

    def test_docs_url_can_be_disabled(self):
        app = Flask(__name__)
        api = ApiFactory(app, docs_url=None)
        self.assertFalse(any(rule.rule == "/docs" for rule in app.url_map.iter_rules()))

    def test_api_error_is_rendered_as_json(self):
        app = Flask(__name__)
        app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite://"
        app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
        db.init_app(app)

        class BoomResource(ApiResource):
            schema = WidgetSchema
            model = Widget

            def get(self, **kwargs):
                abort(403, "Nope", errors={"reason": "forbidden"}, code="forbidden")

        api = ApiFactory(app, db=db, url_prefix="/api")
        api.register_resource(BoomResource, path="/boom", detail=False)
        with app.app_context():
            db.create_all()
        client = app.test_client()

        resp = client.get("/api/boom")
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(resp.get_json(), {
            "message": "Nope",
            "code": "forbidden",
            "errors": {"reason": "forbidden"},
        })

    def test_api_error_defaults_message_from_status(self):
        err = ApiError(404)
        self.assertEqual(err.message, "Not found")
        self.assertEqual(err.to_dict(), {"message": "Not found"})

    def test_openapi_includes_error_component(self):
        spec = self.client.get("/api/openapi.json").get_json()
        self.assertIn("Error", spec["components"]["schemas"])
        self.assertEqual(
            spec["components"]["schemas"]["Error"]["required"],
            ["message"],
        )
        self.assertIn("Error", spec["components"]["responses"])
        self.assertEqual(
            spec["paths"]["/api/widgets"]["get"]["responses"]["400"]["content"]
            ["application/json"]["schema"]["$ref"],
            "#/components/schemas/Error",
        )

    def test_filter_types_override_column_coercion(self):
        app = Flask(__name__)
        app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite://"
        app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
        db.init_app(app)
        api = ApiFactory(app, db=db, url_prefix="/api")
        api.register_resource(TypedFilterWidgetResource, path="/typed")
        with app.app_context():
            db.create_all()
        client = app.test_client()
        client.post("/api/typed", json={"name": "a", "count": 3})

        # filter_types transforms "3" -> 103, which matches no rows.
        resp = client.get("/api/typed?count=3")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json()["data"], [])

        # And the untransformed value matches when the transform is identity.
        resp = client.get("/api/typed?count=-97")  # -97 + 100 == 3
        self.assertEqual(resp.status_code, 200)
        self.assertEqual([w["name"] for w in resp.get_json()["data"]], ["a"])

    def test_eager_loads_accepts_loader_options(self):
        app = Flask(__name__)
        app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite://"
        app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
        db.init_app(app)
        api = ApiFactory(app, db=db, url_prefix="/api")
        api.register_resource(EagerLoadWidgetResource, path="/eager")
        with app.app_context():
            db.create_all()
        client = app.test_client()
        parent = client.post("/api/eager", json={"name": "parent"}).get_json()["data"]
        client.post("/api/eager", json={"name": "child", "parent_id": parent["id"]})

        resp = client.get("/api/eager")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(len(resp.get_json()["data"]), 2)

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
