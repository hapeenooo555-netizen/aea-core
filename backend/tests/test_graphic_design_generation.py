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



def _fake_creative(prompt, aspect_ratio, brand_mode="auto"):
    return {
        "text_overlay": {
            "title": "Biashara yako inaweza zaidi",
            "body": "Tengeneza maudhui na boresha kazi zako za kidigitali.",
            "cta": "Gundua AEA leo",
            "brand": "AEA Affiliate Hub",
            "accent": "#10B981",
        },
        "visual_prompt": "Premium navy and emerald business technology background. No text or logos.",
        "design_rationale": "Clear benefit-led hierarchy.",
        "audience": "Small business owners",
        "objective": "Brand awareness",
        "content_type": "advertisement",
        "brand_applied": True,
        "copy_provider": "groq",
        "copy_model": "test-model",
        "copy_usage": {"total_tokens": 100},
    }

def test_graphic_design_generate_creates_private_unpublished_asset(monkeypatch):
    monkeypatch.setattr(graphic_design, "compose_graphic_design", _fake_creative)
    monkeypatch.setattr(graphic_design, "get_supabase_image_budget_client", lambda: FakeClient())
    monkeypatch.setattr(graphic_design, "get_image_generation_provider", lambda: FakeProvider())
    monkeypatch.setattr(graphic_design, "inspect_generated_background", lambda _content: SimpleNamespace(accepted=True, reason="passed", quality_metrics=None))
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


def test_graphic_design_saved_pin_image_is_looked_up_by_owner_and_job_id(monkeypatch):
    job_id = "11111111-1111-4111-8111-111111111111"
    client = GalleryClient([{"id": job_id, "asset_path": f"owner-123/{job_id}/original.png"}])
    monkeypatch.setattr(graphic_design, "get_supabase_image_budget_client", lambda: client)
    app.dependency_overrides[get_current_user_id] = lambda: "owner-123"
    app.dependency_overrides[get_user_scoped_client] = lambda: client
    response = TestClient(app).get(f"/graphic-design/assets?job_id={job_id}&limit=1")
    assert response.status_code == 200
    assert response.json()["assets"][0]["job_id"] == job_id
    assert client.query.filters == [("owner_id", "owner-123"), ("status", "completed"), ("id", job_id)]


def test_graphic_design_assets_rejects_invalid_limit():
    response = TestClient(app).get("/graphic-design/assets?limit=1000")
    assert response.status_code == 422



def test_graphic_design_generate_renders_exact_text_overlay(monkeypatch):
    monkeypatch.setattr(graphic_design, "compose_graphic_design", _fake_creative)
    monkeypatch.setattr(graphic_design, "get_supabase_image_budget_client", lambda: FakeClient())
    monkeypatch.setattr(graphic_design, "get_image_generation_provider", lambda: FakeProvider())
    monkeypatch.setattr(graphic_design, "inspect_generated_background", lambda _content: SimpleNamespace(accepted=True, reason="passed", quality_metrics=None))
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


def test_graphic_design_generate_accepts_pinterest_portrait_ratio_2_by_3(monkeypatch):
    monkeypatch.setattr(graphic_design, "compose_graphic_design", _fake_creative)
    monkeypatch.setattr(graphic_design, "get_supabase_image_budget_client", lambda: FakeClient())
    monkeypatch.setattr(graphic_design, "get_image_generation_provider", lambda: FakeProvider())
    monkeypatch.setattr(graphic_design, "inspect_generated_background", lambda _content: SimpleNamespace(accepted=True, reason="passed", quality_metrics=None))
    response = TestClient(app).post("/graphic-design/generate", json={
        "prompt": "Create a portrait Pinterest background for an AI business automation product.",
        "aspect_ratio": "2:3",
        "model": "z_image_turbo",
    })
    assert response.status_code == 201
    payload = response.json()
    assert payload["success"] is True
    assert payload["published"] is False
    assert payload["status"] == "completed"
    assert payload["width"] == 1024
    assert payload["height"] == 1280


def test_graphic_design_rejected_background_returns_actionable_quality_error(monkeypatch):
    monkeypatch.setattr(graphic_design, "compose_graphic_design", _fake_creative)
    monkeypatch.setattr(graphic_design, "get_supabase_image_budget_client", lambda: FakeClient())
    monkeypatch.setattr(graphic_design, "get_image_generation_provider", lambda: FakeProvider())
    monkeypatch.setattr(
        graphic_design,
        "inspect_generated_background",
        lambda _content: SimpleNamespace(
            accepted=False,
            reason="unwanted_text_detected",
            quality_metrics={"ocr_tokens": 2},
        ),
    )

    response = TestClient(app).post("/graphic-design/generate", json={
        "prompt": "Create a text-free portrait background for a Pinterest business Pin.",
        "aspect_ratio": "2:3",
        "model": "z_image_turbo",
    })

    assert response.status_code == 422
    assert "text-free visual quality check" in response.json()["detail"]
    assert "No image was saved" in response.json()["detail"]


def test_text_rejection_retries_with_a_fresh_abstract_scene(monkeypatch):
    prompts = []

    class RecordingProvider(FakeProvider):
        async def generate(self, *, prompt, aspect_ratio, model):
            prompts.append(prompt)
            return await super().generate(prompt=prompt, aspect_ratio=aspect_ratio, model=model)

    outcomes = iter((
        SimpleNamespace(accepted=False, reason="unwanted_text_detected", quality_metrics={}),
        SimpleNamespace(accepted=True, reason="passed", quality_metrics={}),
    ))
    monkeypatch.setattr(graphic_design, "compose_graphic_design", _fake_creative)
    monkeypatch.setattr(graphic_design, "get_supabase_image_budget_client", FakeClient)
    monkeypatch.setattr(graphic_design, "get_image_generation_provider", RecordingProvider)
    monkeypatch.setattr(graphic_design, "inspect_generated_background", lambda _: next(outcomes))

    response = TestClient(app).post("/graphic-design/generate", json={
        "prompt": "Create a portrait Pinterest background for an AI business automation product.",
        "aspect_ratio": "2:3",
    })

    assert response.status_code == 201
    assert response.json()["published"] is False
    assert len(prompts) == 2
    assert "business technology background" in prompts[0]
    assert "abstract background" in prompts[1]
    assert "business technology background" not in prompts[1]


def test_private_generation_reuses_durable_job_across_overlapping_calls(monkeypatch):
    import asyncio
    from fastapi import HTTPException

    records = {}

    class JobQuery:
        def __init__(self):
            self.filters = {}
            self.new = None
            self.changes = None

        def select(self, _columns):
            return self

        def limit(self, _count):
            return self

        def eq(self, column, value):
            self.filters[column] = value
            return self

        def insert(self, record):
            self.new = record
            return self

        def update(self, changes):
            self.changes = changes
            return self

        def execute(self):
            if self.new is not None:
                if self.new["id"] in records:
                    raise RuntimeError("duplicate resource")
                records[self.new["id"]] = self.new
                return SimpleNamespace(data=[self.new])
            result = [row for row in records.values()
                      if all(row.get(key) == value for key, value in self.filters.items())]
            if self.changes is not None:
                for row in result:
                    row.update(self.changes)
            return SimpleNamespace(data=result)

    client = SimpleNamespace(table=lambda _table: JobQuery(), storage=Storage())
    monkeypatch.setattr(graphic_design, "compose_graphic_design", _fake_creative)
    monkeypatch.setattr(graphic_design, "get_supabase_image_budget_client", lambda: client)
    monkeypatch.setattr(graphic_design, "inspect_generated_background",
                        lambda data: SimpleNamespace(accepted=True, reason="passed", quality_metrics=None))
    calls = []

    async def exercise():
        started = asyncio.Event()
        finish = asyncio.Event()

        class DelayedProvider(FakeProvider):
            async def generate(self, **kwargs):
                calls.append(kwargs)
                started.set()
                await finish.wait()
                return await super().generate(**kwargs)

        monkeypatch.setattr(graphic_design, "get_image_generation_provider", DelayedProvider)

        def generate():
            return graphic_design.generate_private_design(
                graphic_design.ImageGenerateRequest(prompt="Create a portrait for a bookkeeping guide"),
                "owner-123", client, operation_key="stable-engine-operation",
            )

        first = asyncio.create_task(generate())
        await started.wait()
        try:
            await generate()
        except HTTPException as exc:
            assert exc.status_code == 503
        else:
            raise AssertionError("Pending job was not reported")
        finally:
            finish.set()
        created = await first
        replayed = await generate()
        assert replayed["job_id"] == created["job_id"]
        assert "signed_url" not in created and "signed_url" not in replayed

    asyncio.run(exercise())
    assert len(calls) == len(records) == 1
