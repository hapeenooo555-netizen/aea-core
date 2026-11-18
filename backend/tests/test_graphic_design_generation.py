from __future__ import annotations

from types import SimpleNamespace
import io

from PIL import Image
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
        return {"signedURL": "https://signed.example/image"}


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
        buffer = io.BytesIO()
        Image.new("RGB", (1024, 1280), "white").save(buffer, format="PNG")
        return GeneratedImage(
            content=buffer.getvalue(),
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


class GalleryQuery:
    def __init__(self, rows):
        self.rows = rows
        self.filters = []
        self.selected = None
        self.limit_value = None

    def select(self, columns):
        self.selected = columns
        return self

    def eq(self, column, value):
        self.filters.append((column, value))
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, value):
        self.limit_value = value
        return self

    def execute(self):
        return SimpleNamespace(data=self.rows[:self.limit_value])


class GalleryBucket:
    def __init__(self):
        self.signed_paths = []

    def create_signed_url(self, path, expires_in):
        self.signed_paths.append((path, expires_in))
        return {"signedURL": f"https://signed.example/{path}"}


class GalleryStorage:
    def __init__(self):
        self.bucket = GalleryBucket()

    def from_(self, bucket_name):
        assert bucket_name == "aea-design-assets"
        return self.bucket


class GalleryClient:
    def __init__(self, rows):
        self.query = GalleryQuery(rows)
        self.storage = GalleryStorage()

    def table(self, table_name):
        assert table_name == "graphic_design_jobs"
        return self.query


def test_graphic_design_assets_lists_only_owner_paths_with_temporary_urls(monkeypatch):
    rows = [
        {
            "id": "asset-1",
            "owner_id": "owner-123",
            "prompt": "Tanzanian small business poster",
            "aspect_ratio": "1:1",
            "provider": "fal.ai",
            "model": "fal-ai/z-image/turbo",
            "status": "completed",
            "asset_path": "owner-123/asset-1/original.png",
            "mime_type": "image/png",
            "width": 1024,
            "height": 1024,
            "created_at": "2026-11-18T10:00:00Z",
        },
        {
            "id": "asset-foreign",
            "owner_id": "another-user",
            "prompt": "must not be exposed",
            "aspect_ratio": "1:1",
            "provider": "fal.ai",
            "model": "fal-ai/z-image/turbo",
            "status": "completed",
            "asset_path": "another-user/asset-foreign/original.png",
            "mime_type": "image/png",
            "width": 1024,
            "height": 1024,
            "created_at": "2026-11-18T09:00:00Z",
        },
    ]
    client = GalleryClient(rows)
    monkeypatch.setattr(graphic_design, "get_supabase_image_budget_client", lambda: client)
    app.dependency_overrides[get_user_scoped_client] = lambda: client

    response = TestClient(app).get("/graphic-design/assets?limit=10")

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["count"] == 1
    assert payload["assets"][0]["job_id"] == "asset-1"
    assert payload["assets"][0]["signed_url"] == (
        "https://signed.example/owner-123/asset-1/original.png"
    )
    assert payload["assets"][0]["signed_url_expires_in"] == 3600
    assert client.query.filters == [("owner_id", "owner-123"), ("status", "completed")]
    assert client.query.limit_value == 10
    assert client.storage.bucket.signed_paths == [
        ("owner-123/asset-1/original.png", 3600)
    ]


def test_graphic_design_assets_requires_authentication():
    app.dependency_overrides.clear()
    response = TestClient(app).get("/graphic-design/assets")
    assert response.status_code == 401


def test_graphic_design_assets_rejects_invalid_limit():
    response = TestClient(app).get("/graphic-design/assets?limit=1000")
    assert response.status_code == 422



def test_graphic_design_generate_renders_exact_text_overlay(monkeypatch):
    monkeypatch.setattr(graphic_design, "get_supabase_image_budget_client", lambda: FakeClient())
    monkeypatch.setattr(graphic_design, "get_image_generation_provider", lambda: FakeProvider())
    response = TestClient(app).post("/graphic-design/generate", json={
        "prompt": "Create a clean abstract blue and green business background without text.",
        "aspect_ratio": "4:5",
        "model": "z_image_turbo",
        "text_overlay": {
            "title": "AI inarahisisha biashara yako",
            "body": "Jifunze jinsi AEA inavyosaidia biashara yako kukua.",
            "cta": "Gundua sasa",
            "brand": "AEA Affiliate Hub",
            "accent": "#10B981",
        },
    })
    assert response.status_code == 201
    payload = response.json()
    assert payload["mime_type"] == "image/png"
    assert payload["width"] == 1024
    assert payload["height"] == 1280
    assert payload["published"] is False
