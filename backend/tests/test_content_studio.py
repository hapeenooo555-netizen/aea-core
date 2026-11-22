from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.dependencies import get_current_user_id, get_user_scoped_client
from app.main import app
from app.routers import content_studio


class FakeProvider:
    def generate(self, prompt: str, system_prompt: str | None = None):
        assert "You are AEA Content Studio" in (system_prompt or "")
        assert "fluent, natural, grammatically correct Tanzanian Kiswahili" in (system_prompt or "")
        assert "Never invent product features" in (system_prompt or "")
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
    assert payload["draft"]["caption"] == "Learn practical AI workflows."
    assert payload["draft"]["cta"] == "Get the guide"

def test_emoji_enrichment_does_not_force_repeated_emojis():
    draft = {
        "caption": "Ongea nasi leo",
        "cta": "Tuma ujumbe sasa",
        "hooks": ["Unahitaji huduma nzuri?"],
        "platform_variants": {"facebook": {"caption": "Huduma bora kwa biashara yako"}},
    }
    enriched = content_studio._enrich_draft_emojis(draft, {"prompt": "AI biashara", "tone": "friendly"})
    assert enriched == draft

def test_emoji_enrichment_preserves_model_selected_emojis_and_plain_text():
    draft = {"caption": "Karibu dukani 🛍️", "cta": "Wasiliana nasi"}
    enriched = content_studio._enrich_draft_emojis(
        draft, {"prompt": "Ofa ya punguzo kwa wateja", "tone": "professional"}
    )
    assert enriched["caption"] == "Karibu dukani 🛍️"
    assert enriched["cta"] == "Wasiliana nasi"

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


def test_emoji_enrichment_keeps_platform_variants_unchanged():
    draft = {
        "caption": "Rahisisha kazi zako",
        "cta": "Anza kutumia AEA",
        "hooks": ["Unatumia muda mwingi kwenye kazi za kawaida?"],
        "platform_variants": {"instagram": {"caption": "Fanya kazi kwa haraka", "cta": "Jaribu sasa"}},
    }
    assert content_studio._enrich_draft_emojis(draft, {"prompt": "AI automation", "tone": "exciting"}) == draft

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


def test_studio_accepts_structured_research_sources_and_labels_them_unverified(monkeypatch):
    monkeypatch.setattr(content_studio, "get_llm_provider", lambda: FakeProvider())
    client = TestClient(app)
    response = client.post(
        "/content/studio/generate",
        json={
            "prompt": "Create a practical guide to customer follow-up for small businesses.",
            "niche": "Business automation",
            "content_type": "article",
            "audience": "Small business owners",
            "objective": "education",
            "platforms": ["facebook"],
            "research_sources": [{
                "title": "Digital adoption survey",
                "publisher": "Example Research Group",
                "url": "https://example.org/research/digital-adoption",
                "excerpt": "Respondents described barriers to adopting digital tools.",
                "published_at": "2026-10-01",
                "retrieved_at": "2026-11-18",
                "source_type": "research",
            }],
        },
    )
    assert response.status_code == 200
    intelligence = response.json()["draft"]["intelligence"]
    assert intelligence["market_research"]["status"] == "user_sources_supplied_not_independently_verified"
    assert intelligence["market_research"]["sources"][0]["publisher"] == "Example Research Group"
    assert intelligence["opportunity_assessment"]["demand_status"] == "unknown"


def test_studio_rejects_invalid_research_source_url(monkeypatch):
    monkeypatch.setattr(content_studio, "get_llm_provider", lambda: FakeProvider())
    response = TestClient(app).post(
        "/content/studio/generate",
        json={
            "prompt": "Create a practical guide to customer follow-up for small businesses.",
            "niche": "Business automation",
            "content_type": "article",
            "research_sources": [{
                "title": "Bad source",
                "publisher": "Unknown",
                "url": "javascript:alert(1)",
                "excerpt": "This is an example excerpt long enough for validation.",
            }],
        },
    )
    assert response.status_code == 422


def test_studio_accepts_tanzania_and_international_market_scope(monkeypatch):
    monkeypatch.setattr(content_studio, "get_llm_provider", lambda: FakeProvider())
    response = TestClient(app).post("/content/studio/generate", json={
        "prompt": "Create a market-aware guide for small business customer follow-up.",
        "niche": "Business automation", "content_type": "article",
        "market_scope": "tanzania_and_international", "target_countries": ["KE", "US"],
    })
    assert response.status_code == 200
    scope = response.json()["draft"]["intelligence"]["geographic_scope"]
    assert scope["tanzania"] is True and scope["international"] is True
    assert scope["target_countries"] == ["KE", "US"]


def test_studio_rejects_http_research_source_url(monkeypatch):
    monkeypatch.setattr(content_studio, "get_llm_provider", lambda: FakeProvider())
    response = TestClient(app).post(
        "/content/studio/generate",
        json={
            "prompt": "Create a practical guide to customer follow-up for small businesses.",
            "niche": "Business automation",
            "content_type": "article",
            "research_sources": [{
                "title": "Insecure source",
                "publisher": "Example",
                "url": "http://example.org/research",
                "excerpt": "This excerpt is long enough for the source validation requirement.",
            }],
        },
    )
    assert response.status_code == 422


def test_world_bank_endpoint_requires_authentication():
    app.dependency_overrides.pop(get_current_user_id, None)
    response = TestClient(app).get("/content/studio/research/world-bank?country_codes=TZ")
    assert response.status_code == 401


def test_world_bank_endpoint_returns_country_context(monkeypatch):
    async def fake_fetch(countries, indicators):
        return {
            "provider": "world_bank_indicators_api",
            "status": "retrieved",
            "countries_requested": countries,
            "indicators_requested": indicators,
            "observations": [{"country_code": "TZ", "year": "2024", "value": 100}],
            "errors": [],
            "market_demand_assessment": "not_assessed",
        }

    monkeypatch.setattr(content_studio, "fetch_world_bank_indicators", fake_fetch)
    app.dependency_overrides[get_current_user_id] = lambda: "owner-123"
    try:
        response = TestClient(app).get(
            "/content/studio/research/world-bank?country_codes=TZ&indicators=population"
        )
        assert response.status_code == 200
        assert response.json()["observations"][0]["country_code"] == "TZ"
        assert response.json()["market_demand_assessment"] == "not_assessed"
    finally:
        app.dependency_overrides.pop(get_current_user_id, None)



def test_country_intelligence_markets_endpoint_requires_authentication():
    app.dependency_overrides.clear()
    response = TestClient(app).get("/content/studio/country-intelligence/markets")
    assert response.status_code == 401


def test_country_intelligence_plan_endpoint_returns_country_specific_conversion_paths():
    response = TestClient(app).post("/content/studio/country-intelligence/plan", json={
        "country_codes": ["TZ", "KE", "US"],
        "offer": "Small business AI toolkit",
        "objective": "sales",
        "channel": "facebook",
    })
    assert response.status_code == 200
    payload = response.json()
    assert payload["strategy"] == "content_to_revenue"
    assert [plan["country_code"] for plan in payload["plans"]] == ["TZ", "KE", "US"]
    assert [plan["currency"] for plan in payload["plans"]] == ["TZS", "KES", "USD"]
    assert all(plan["primary_conversion_event"] == "purchase_confirmed" for plan in payload["plans"])


def test_country_intelligence_plan_endpoint_rejects_unknown_market():
    response = TestClient(app).post("/content/studio/country-intelligence/plan", json={
        "country_codes": ["XX"], "offer": "A useful offer"
    })
    assert response.status_code == 422



def test_studio_generate_accepts_json_inside_markdown_fence(monkeypatch):
    class FencedProvider(FakeProvider):
        def generate(self, prompt: str, system_prompt: str | None = None):
            response = super().generate(prompt, system_prompt)
            response["content"] = "```json\n" + response["content"] + "\n```"
            return response

    monkeypatch.setattr(content_studio, "get_llm_provider", lambda: FencedProvider())
    response = TestClient(app).post("/content/studio/generate", json={
        "prompt": "Create a practical introductory guide about AI for small businesses.",
        "niche": "AI and business automation",
        "content_type": "article",
    })
    assert response.status_code == 200
    assert response.json()["draft"]["title"] == "AI for Small Business"


def test_studio_generate_accepts_json_after_short_provider_preamble(monkeypatch):
    class PreambleProvider(FakeProvider):
        def generate(self, prompt: str, system_prompt: str | None = None):
            response = super().generate(prompt, system_prompt)
            response["content"] = "Here is the requested JSON:\n" + response["content"]
            return response

    monkeypatch.setattr(content_studio, "get_llm_provider", lambda: PreambleProvider())
    response = TestClient(app).post("/content/studio/generate", json={
        "prompt": "Create a practical introductory guide about AI for small businesses.",
        "niche": "AI and business automation",
        "content_type": "article",
    })
    assert response.status_code == 200
    assert response.json()["draft"]["title"] == "AI for Small Business"


def test_studio_json_parser_rejects_non_object_and_malformed_output():
    assert content_studio._parse_json_object('["not", "an", "object"]') is None
    assert content_studio._parse_json_object('{"title": ') is None
