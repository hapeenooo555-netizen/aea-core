from __future__ import annotations

from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.dependencies import get_current_user_id, get_user_scoped_client
from app.main import app
from app.routers import graphic_design


class Query:
    def __init__(self, row):
        self.row = row
        self.payload = None

    def insert(self, payload):
        self.payload = payload
        return self

    def update(self, payload):
        self.payload = payload
        return self

    def eq(self, *_args):
        return self

    def execute(self):
        return SimpleNamespace(data=[self.payload or self.row or {"id": "job-1"}])


class StorageBucket:
    def upload(self, *_args, **_kwargs):
        return SimpleNamespace(data={"path": "owner/job/original.png"})

    def create_signed_url(self, *_args, **_kwargs):
        return SimpleNamespace(data={"signedUrl": "https://signed.example/image"})


class Storage:
    def from_(self, _bucket):
        return StorageBucket()


class FakeClient:
    def table(self, _table):
        return Query({"id": "job-1"})

    storage = Storage()


class FakeProvider:
    async def generate(self, *, prompt, aspect_ratio, model):
        from app.services.image_generation_provider import GeneratedImage
        return GeneratedImage(
            content=b"png-bytes",
            mime_type="image/png",
            width=1024,
            height=1024,
            provider="fal.ai",
            model="fal-ai/z-image/turbo",
            provider_metadata={"request_id": "test-request"},
        )


def setup_function():
    app.dependency_overrides[get_current_user_id] = lambda: "owner-123"
    app.dependency_overrides[get_user_scoped_client] = lambda: FakeClient()


def teardown_function():
    app.dependency_overrides.clear()


def test_graphic_design_generate_creates_private_unpublished_asset(monkeypatch):
    monkeypatch.setattr(graphic_design, "get_supabase_image_budget_client", lambda: FakeClient())
    monkeypatch.setattr(graphic_design, "get_image_generation_provider", lambda: FakeProvider())
    response = TestClient(app).post("/graphic-design/generate", json={
        "prompt": "Create a clean business poster for a Tanzanian small business.",
        "aspect_ratio": "1:1",
        "model": "z_image_turbo",
    })
    assert response.status_code == 201
    payload = response.json()
    assert payload["success"] is True
    assert payload["published"] is False
    assert payload["provider"] == "fal.ai"
    assert payload["signed_url"] == "https://signed.example/image"
    assert payload["asset_path"].startswith("owner-123/")


def test_graphic_design_generate_requires_authentication():
    app.dependency_overrides.clear()
    response = TestClient(app).post("/graphic-design/generate", json={
        "prompt": "Create a clean business poster for a Tanzanian small business."
    })
    assert response.status_code == 401


def test_graphic_design_generate_rejects_invalid_model():
    response = TestClient(app).post("/graphic-design/generate", json={
        "prompt": "Create a clean business poster for a Tanzanian small business.",
        "model": "unknown-model",
    })
    assert response.status_code == 422
