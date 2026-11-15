from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.dependencies import get_current_user_id
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
