from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.dependencies import get_current_user_id, get_user_scoped_client
from app.routers import creator_community


class Response:
    def __init__(self, data):
        self.data = data


class Table:
    def __init__(self, db, name):
        self.db, self.name = db, name
        self.filters, self.payload = {}, None
        self.action = "select"

    def select(self, *_args):
        if self.payload is None:
            self.action = "select"
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def in_(self, key, values):
        self.filters[key] = set(values)
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, *_args):
        return self

    def insert(self, payload):
        self.action, self.payload = "insert", payload
        return self

    def upsert(self, payload, **_kwargs):
        self.action, self.payload = "upsert", payload
        return self

    def update(self, payload):
        self.action, self.payload = "update", payload
        return self

    def delete(self):
        self.action = "delete"
        return self

    def execute(self):
        rows = self.db.tables.setdefault(self.name, [])
        matches = [r for r in rows if all(
            (r.get(k) in v if isinstance(v, set) else r.get(k) == v)
            for k, v in self.filters.items()
        )]
        if self.action in {"insert", "upsert"}:
            row = dict(self.payload)
            if self.action == "upsert":
                key = "user_id" if self.name == "creator_profiles" else (
                    "product_id" if self.name == "creator_product_reactions" else "follower_id"
                )
                for old in rows:
                    if old.get(key) == row.get(key):
                        old.update(row)
                        return Response([old])
            row.setdefault("id", "33333333-3333-4333-8333-333333333333")
            rows.append(row)
            return Response([row])
        if self.action == "update":
            for row in matches:
                row.update(self.payload)
            return Response(matches)
        if self.action == "delete":
            self.db.tables[self.name] = [r for r in rows if r not in matches]
            return Response([])
        return Response(matches)


class Database:
    def __init__(self):
        self.tables = {
            "creator_products": [
                {"id": "11111111-1111-4111-8111-111111111111", "owner_id": "owner-1",
                 "title": "Public guide", "visibility": "community_public", "status": "listed",
                 "age_rating": "general", "is_sensitive": False, "created_at": "2026-01-01"}
            ],
            "creator_profiles": [
                {"user_id": "owner-1", "display_name": "Creator One", "bio": "", "is_public": True}
            ],
            "creator_age_verifications": [],
            "creator_product_reactions": [],
            "creator_product_comments": [],
            "creator_follows": [],
        }

    def table(self, name):
        return Table(self, name)


def client(db=None, user="viewer-1"):
    app = FastAPI()
    app.include_router(creator_community.router)
    app.dependency_overrides[get_current_user_id] = lambda: user
    app.dependency_overrides[get_user_scoped_client] = lambda: db or Database()
    return TestClient(app)


def test_feed_returns_only_community_public_items_with_public_profiles():
    db = Database()
    db.tables["creator_products"].append({
        "id": "22222222-2222-4222-8222-222222222222", "owner_id": "owner-1",
        "title": "Private item", "visibility": "private", "status": "draft",
        "age_rating": "general", "is_sensitive": False,
    })
    response = client(db).get("/community/feed")
    assert response.status_code == 200
    assert [item["title"] for item in response.json()["items"]] == ["Public guide"]


def test_creator_can_request_18_plus_only_after_verified_age_status():
    db = Database()
    api = client(db, user="owner-1")
    product_id = "11111111-1111-4111-8111-111111111111"
    denied = api.patch(f"/community/products/{product_id}/visibility", json={
        "visibility": "community_public", "is_sensitive": True, "age_rating": "18_plus"
    })
    assert denied.status_code == 403
    db.tables["creator_age_verifications"].append({"user_id": "owner-1", "status": "verified"})
    allowed = api.patch(f"/community/products/{product_id}/visibility", json={
        "visibility": "community_public", "is_sensitive": True, "age_rating": "18_plus"
    })
    assert allowed.status_code == 200


def test_user_cannot_self_verify_age():
    db = Database()
    response = client(db, user="owner-1").post("/community/me/age-verification", json={})
    assert response.status_code == 202
    assert response.json()["verification"]["status"] == "pending"


def test_creator_profile_is_saved_with_owner_identity_from_auth():
    db = Database()
    response = client(db, user="owner-1").put("/community/me/profile", json={
        "display_name": "Creator One", "bio": "A creator profile", "is_public": True
    })
    assert response.status_code == 200
    assert response.json()["profile"]["user_id"] == "owner-1"
    assert response.json()["profile"]["is_public"] is True
    assert response.json()["profile"]["display_name"] == "Creator One"



def test_user_can_react_and_comment_on_public_general_content():
    db = Database()
    api = client(db, user="viewer-1")
    product_id = "11111111-1111-4111-8111-111111111111"
    reaction = api.put(f"/community/products/{product_id}/reaction", json={"reaction": "like"})
    comment = api.post(f"/community/products/{product_id}/comments", json={"body": "Very useful work."})
    assert reaction.status_code == 200
    assert comment.status_code == 201
    assert db.tables["creator_product_reactions"][0]["user_id"] == "viewer-1"
    assert reaction.json()["reaction"]["reaction"] == "like"
    assert reaction.json()["reaction"]["product_id"] == product_id
    assert db.tables["creator_product_comments"][0]["user_id"] == "viewer-1"
    assert comment.json()["comment"]["body"] == "Very useful work."
    assert comment.json()["comment"]["product_id"] == product_id


def test_creator_cannot_follow_self():
    response = client(Database(), user="11111111-1111-4111-8111-111111111111").post(
        "/community/creators/11111111-1111-4111-8111-111111111111/follow"
    )
    assert response.status_code == 422



def test_creator_can_follow_another_public_creator_and_receives_saved_record():
    response = client(Database(), user="viewer-1").post(
        "/community/creators/11111111-1111-4111-8111-111111111111/follow"
    )
    assert response.status_code == 201
    assert response.json()["created"] is True
    assert response.json()["follow"]["follower_id"] == "viewer-1"
    assert response.json()["follow"]["creator_id"] == "11111111-1111-4111-8111-111111111111"


def test_feed_hides_public_products_when_creator_profile_is_private():
    db = Database()
    db.tables["creator_profiles"][0]["is_public"] = False
    response = client(db).get("/community/feed")
    assert response.status_code == 200
    assert response.json()["items"] == []


def test_visibility_rejects_18_plus_without_sensitive_warning():
    api = client(Database(), user="owner-1")
    response = api.patch(
        "/community/products/11111111-1111-4111-8111-111111111111/visibility",
        json={"visibility": "community_public", "is_sensitive": False, "age_rating": "18_plus"},
    )
    assert response.status_code == 422
