from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.dependencies import get_current_user_id, get_user_scoped_client
from app.main import app
from app.routers import content_studio


class FakeProvider:
    def generate(self, prompt: str, system_prompt: str | None = None):
        assert "You are AEA Content Studio" in (system_prompt or "")
        return {
            "success": True,
            "provider": "test",
            "model": "test-model",
            "content": json.dumps({
                "title": "AI for Small Business",
                "content_type": "ebook",
                "core_content": {"table_of_contents": ["Introduction", "Chapter 1"]},
                "hooks": ["Hook one", "Hook two", "Hook three"],
                "caption": "Learn practical AI workflows.",
                "cta": "Get the guide",
                "hashtags": ["#AI", "#SmallBusiness"],
            }),
        }


class FakeResponse:
    def __init__(self, data):
        self.data = data


class FakeTable:
    def __init__(self, rows):
        self.rows = rows
        self.filters = {}
        self.insert_payload = None
        self.sort_desc = False

    def select(self, *_args):
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def order(self, _key, desc=False):
        self.sort_desc = desc
        return self

    def limit(self, _limit):
        return self

    def insert(self, payload):
        self.insert_payload = payload
        return self

    def execute(self):
        rows = [row for row in self.rows if all(row.get(k) == v for k, v in self.filters.items())]
        if self.insert_payload is not None:
            row = dict(self.insert_payload)
            row["id"] = "version-row-id"
            self.rows.append(row)
            return FakeResponse([row])
        if self.sort_desc:
            rows.sort(key=lambda row: row.get("version", 0), reverse=True)
        return FakeResponse(rows)


class FakeDatabase:
    def __init__(self):
        self.rows = []

    def table(self, _name):
        return FakeTable(self.rows)


def setup_function():
    app.dependency_overrides[get_current_user_id] = lambda: "owner-123"


def teardown_function():
    app.dependency_overrides.clear()


def test_studio_generate_returns_structured_unpublished_draft(monkeypatch):
    monkeypatch.setattr(content_studio, "get_llm_provider", lambda: FakeProvider())
    client = TestClient(app)
    response = client.post(
        "/content/studio/generate",
        json={
            "prompt": "Create an introductory e-book about AI automation for small businesses.",
            "niche": "AI and business automation",
            "content_type": "ebook",
            "audience": "Small business owners",
            "objective": "leads",
            "language": "English",
            "tone": "practical",
            "platforms": ["pinterest", "facebook"],
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["status"] == "draft"
    assert payload["published"] is False
    assert payload["draft"]["title"] == "AI for Small Business"
    assert len(payload["draft"]["hooks"]) == 3


def test_studio_generate_rejects_mock_provider(monkeypatch):
    from app.services.llm_provider import MockLLMProvider

    monkeypatch.setattr(content_studio, "get_llm_provider", lambda: MockLLMProvider())
    client = TestClient(app)
    response = client.post(
        "/content/studio/generate",
        json={
            "prompt": "Create an educational short video about solar energy.",
            "niche": "Education",
            "content_type": "short_video",
        },
    )
    assert response.status_code == 503
    assert "not configured" in response.json()["detail"].lower()


def test_studio_generate_requires_authentication():
    app.dependency_overrides.clear()
    client = TestClient(app)
    response = client.post(
        "/content/studio/generate",
        json={
            "prompt": "Write a short story about a young entrepreneur.",
            "niche": "Storytelling",
            "content_type": "storytelling",
        },
    )
    assert response.status_code == 401


def test_create_asset_persists_owner_scoped_version_one():
    db = FakeDatabase()
    app.dependency_overrides[get_user_scoped_client] = lambda: db
    response = TestClient(app).post("/content/studio/assets", json={
        "idempotency_key": "draft-create-123456",
        "title": "AI for Small Business",
        "content_type": "ebook",
        "niche": "AI",
        "prompt": "Create an e-book",
        "content": {"title": "AI for Small Business"},
    })
    assert response.status_code == 201
    assert response.json()["asset"]["owner_id"] == "owner-123"
    assert response.json()["asset"]["version"] == 1
    assert response.json()["asset"]["status"] == "draft"


def test_create_asset_replays_idempotency_key_without_duplicate():
    db = FakeDatabase()
    db.rows.append({
        "id": "existing",
        "asset_id": "11111111-1111-4111-8111-111111111111",
        "owner_id": "owner-123",
        "version": 1,
        "idempotency_key": "draft-create-123456",
    })
    app.dependency_overrides[get_user_scoped_client] = lambda: db
    response = TestClient(app).post("/content/studio/assets", json={
        "idempotency_key": "draft-create-123456",
        "title": "AI for Small Business",
        "content_type": "ebook",
        "niche": "AI",
        "prompt": "Create an e-book",
        "content": {"title": "AI for Small Business"},
    })
    assert response.status_code == 201
    assert response.json()["replayed"] is True
    assert len(db.rows) == 1


def test_list_asset_versions_is_scoped_to_authenticated_owner():
    db = FakeDatabase()
    db.rows.extend([
        {"asset_id": "11111111-1111-4111-8111-111111111111", "owner_id": "owner-123", "version": 1},
        {"asset_id": "11111111-1111-4111-8111-111111111111", "owner_id": "another-user", "version": 9},
    ])
    app.dependency_overrides[get_user_scoped_client] = lambda: db
    response = TestClient(app).get("/content/studio/assets/11111111-1111-4111-8111-111111111111/versions")
    assert response.status_code == 200
    assert response.json()["versions"] == [
        {"asset_id": "11111111-1111-4111-8111-111111111111", "owner_id": "owner-123", "version": 1}
    ]


def test_revise_asset_requires_expected_current_version():
    db = FakeDatabase()
    db.rows.append({
        "asset_id": "11111111-1111-4111-8111-111111111111",
        "owner_id": "owner-123",
        "version": 2,
        "idempotency_key": "old-save-key-123456",
    })
    app.dependency_overrides[get_user_scoped_client] = lambda: db
    response = TestClient(app).patch("/content/studio/assets/11111111-1111-4111-8111-111111111111", json={
        "idempotency_key": "new-save-key-123456",
        "expected_version": 1,
        "title": "Edited title",
        "content_type": "ebook",
        "niche": "AI",
        "prompt": "Create an e-book",
        "content": {"title": "Edited title"},
    })
    assert response.status_code == 409
