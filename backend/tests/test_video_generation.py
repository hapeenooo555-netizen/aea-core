from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.dependencies import get_current_user_id, get_user_scoped_client
from app.routers import video_generation


class Response:
    def __init__(self, data):
        self.data = data


class Query:
    def __init__(self, db, table):
        self.db, self.table = db, table
        self.filters, self.payload, self.action = {}, None, "select"

    def select(self, *_args):
        if self.action != "update": self.action = "select"
        return self
    def eq(self, key, value): self.filters[key] = value; return self
    def limit(self, *_args): return self
    def order(self, *_args, **_kwargs): return self
    def insert(self, payload): self.action, self.payload = "insert", payload; return self
    def update(self, payload): self.action, self.payload = "update", payload; return self
    def delete(self): self.action = "delete"; return self

    def execute(self):
        rows = self.db.setdefault(self.table, [])
        matches = [r for r in rows if all(r.get(k) == v for k, v in self.filters.items())]
        if self.action == "insert":
            inserted = self.payload if isinstance(self.payload, list) else [self.payload]
            rows.extend(dict(item) for item in inserted)
            return Response(inserted)
        if self.action == "update":
            for row in matches: row.update(self.payload)
            return Response(matches)
        if self.action == "delete":
            self.db[self.table] = [row for row in rows if row not in matches]
            return Response(matches)
        return Response(matches)


class DB:
    def __init__(self): self.tables = {}
    def table(self, name): return Query(self.tables, name)


def make_client(db):
    app = FastAPI()
    app.include_router(video_generation.router)
    app.dependency_overrides[get_current_user_id] = lambda: "user-1"
    app.dependency_overrides[get_user_scoped_client] = lambda: db
    return TestClient(app)


def test_video_generation_requires_runway_secret(monkeypatch):
    monkeypatch.delenv("RUNWAYML_API_SECRET", raising=False)
    response = make_client(DB()).post("/video/generations", json={
        "prompt": "Create a vertical advert for AEA in Kiswahili."
    })
    assert response.status_code == 503
    assert "RUNWAYML_API_SECRET" in response.json()["detail"]


def test_video_generation_rejects_invalid_duration():
    response = make_client(DB()).post("/video/generations", json={
        "prompt": "Create a vertical advert for AEA in Kiswahili.",
        "duration_seconds": 30
    })
    assert response.status_code == 422


def test_video_job_is_owner_scoped():
    db = DB()
    db.tables["video_generation_jobs"] = [{
        "id": "22222222-2222-4222-8222-222222222222",
        "owner_id": "another-user",
        "provider_task_id": "runway-task",
        "model": "gen4.5",
        "status": "pending",
    }]
    response = make_client(db).get("/video/generations/22222222-2222-4222-8222-222222222222")
    assert response.status_code == 404



def test_prompt_planner_creates_long_form_scene_plan(monkeypatch):
    class Provider:
        name = "groq"

        def generate(self, **_kwargs):
            import json
            return {
                "success": True,
                "content": json.dumps({
                    "title": "Hadithi ya Mjasiriamali",
                    "scenes": [
                        {
                            "title": f"Scene {index}",
                            "narration": "Simulizi ya biashara",
                            "visual_prompt": f"Cinematic realistic scene {index}",
                            "duration_seconds": 10,
                        }
                        for index in range(27)
                    ],
                    "creative_notes": ["Maintain consistent main character"],
                }),
            }

    monkeypatch.setattr(video_generation, "get_llm_provider", lambda: Provider())
    db = DB()
    response = make_client(db).post("/video/generations/projects/plan", json={
        "prompt": "Tengeneza documentary kuhusu safari ya mjasiriamali wa Tanzania.",
        "title": "Safari ya Mjasiriamali",
        "format": "documentary",
        "target_duration_seconds": 270,
        "language": "Kiswahili",
        "presenter_mode": "realistic_human",
    })
    assert response.status_code == 201
    payload = response.json()
    assert payload["project"]["scene_count"] == 27
    assert payload["project"]["planned_duration_seconds"] == 270
    assert len(db.tables["video_project_scenes"]) == 27
    assert payload["published"] is False


def test_prompt_planner_rejects_short_storyboard(monkeypatch):
    class Provider:
        name = "groq"

        def generate(self, **_kwargs):
            import json
            return {
                "success": True,
                "content": json.dumps({
                    "title": "Too short",
                    "scenes": [{
                        "title": "Opening",
                        "narration": "Start",
                        "visual_prompt": "Wide cinematic shot",
                        "duration_seconds": 5,
                    }],
                }),
            }

    monkeypatch.setattr(video_generation, "get_llm_provider", lambda: Provider())
    response = make_client(DB()).post("/video/generations/projects/plan", json={
        "prompt": "Tengeneza documentary ndefu kuhusu biashara.",
        "target_duration_seconds": 300,
    })
    assert response.status_code == 502



def test_prompt_planner_repairs_short_storyboard_once(monkeypatch):
    class Provider:
        name = "groq"

        def __init__(self):
            self.calls = 0

        def generate(self, **_kwargs):
            import json
            self.calls += 1
            scene_count = 12 if self.calls == 1 else 30
            return {
                "success": True,
                "content": json.dumps({
                    "title": "Safari ya Mjasiriamali",
                    "scenes": [
                        {
                            "title": f"Scene {index}",
                            "narration": "Safari ya biashara inaendelea",
                            "visual_prompt": f"Cinematic faceless scene {index}",
                            "duration_seconds": 10,
                        }
                        for index in range(scene_count)
                    ],
                }),
            }

    provider = Provider()
    monkeypatch.setattr(video_generation, "get_llm_provider", lambda: provider)
    db = DB()
    response = make_client(db).post("/video/generations/projects/plan", json={
        "prompt": "Tengeneza documentary ya safari ya mjasiriamali wa Tanzania.",
        "title": "Safari ya Mjasiriamali",
        "format": "storytelling",
        "target_duration_seconds": 300,
        "language": "Kiswahili",
        "presenter_mode": "faceless",
        "aspect_ratio": "16:9",
    })

    assert response.status_code == 201
    assert provider.calls == 2
    payload = response.json()
    assert payload["project"]["planned_duration_seconds"] == 300
    assert payload["project"]["scene_count"] == 30
    assert len(db.tables["video_project_scenes"]) == 30

def test_video_batch_rejects_more_than_twenty_scenes():
    scene_ids = [
        f"00000000-0000-4000-8000-{index:012d}"
        for index in range(1, 22)
    ]
    response = make_client(DB()).post(
        "/video/generations/projects/22222222-2222-4222-8222-222222222222/generate-batch",
        json={"scene_ids": scene_ids},
    )
    assert response.status_code == 422


def test_subtitle_export_builds_valid_timed_srt():
    db = DB()
    project_id = "33333333-3333-4333-8333-333333333333"
    db.tables["video_projects"] = [{
        "id": project_id,
        "owner_id": "user-1",
        "target_duration_seconds": 20,
    }]
    db.tables["video_project_scenes"] = [
        {
            "id": "44444444-4444-4444-8444-444444444444",
            "project_id": project_id,
            "owner_id": "user-1",
            "scene_number": 1,
            "narration": "Karibu kwenye hadithi yetu.",
            "duration_seconds": 5,
        },
        {
            "id": "55555555-5555-4555-8555-555555555555",
            "project_id": project_id,
            "owner_id": "user-1",
            "scene_number": 2,
            "narration": "Safari inaanza sasa.",
            "duration_seconds": 7,
        },
    ]
    response = make_client(db).get(f"/video/generations/projects/{project_id}/subtitles.srt")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-subrip")
    assert "00:00:00,000 --> 00:00:05,000" in response.text
    assert "00:00:05,000 --> 00:00:12,000" in response.text
    assert "Karibu kwenye hadithi yetu." in response.text


def test_subtitle_export_is_owner_scoped():
    db = DB()
    project_id = "33333333-3333-4333-8333-333333333333"
    db.tables["video_projects"] = [{
        "id": project_id,
        "owner_id": "another-user",
        "target_duration_seconds": 20,
    }]
    response = make_client(db).get(f"/video/generations/projects/{project_id}/subtitles.srt")
    assert response.status_code == 404


def test_scene_narration_requires_provider_key(monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    project_id = "33333333-3333-4333-8333-333333333333"
    scene_id = "44444444-4444-4444-8444-444444444444"
    db = DB()
    db.tables["video_projects"] = [{"id": project_id, "owner_id": "user-1"}]
    db.tables["video_project_scenes"] = [{
        "id": scene_id,
        "project_id": project_id,
        "owner_id": "user-1",
        "narration": "Karibu Tanzania.",
    }]
    response = make_client(db).post(
        f"/video/generations/projects/{project_id}/scenes/{scene_id}/narration",
        json={"voice_id": "voice-test"},
    )
    assert response.status_code == 503
    assert "ELEVENLABS_API_KEY" in response.json()["detail"]


def test_scene_narration_is_owner_scoped(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    project_id = "33333333-3333-4333-8333-333333333333"
    scene_id = "44444444-4444-4444-8444-444444444444"
    db = DB()
    db.tables["video_projects"] = [{"id": project_id, "owner_id": "other-user"}]
    response = make_client(db).post(
        f"/video/generations/projects/{project_id}/scenes/{scene_id}/narration",
        json={"voice_id": "voice-test"},
    )
    assert response.status_code == 404


def test_scene_narration_rejects_empty_text(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    project_id = "33333333-3333-4333-8333-333333333333"
    scene_id = "44444444-4444-4444-8444-444444444444"
    db = DB()
    db.tables["video_projects"] = [{"id": project_id, "owner_id": "user-1"}]
    db.tables["video_project_scenes"] = [{
        "id": scene_id,
        "project_id": project_id,
        "owner_id": "user-1",
        "narration": "",
    }]
    response = make_client(db).post(
        f"/video/generations/projects/{project_id}/scenes/{scene_id}/narration",
        json={"voice_id": "voice-test"},
    )
    assert response.status_code == 409


def test_scene_narration_signed_url_is_owner_scoped():
    project_id = "33333333-3333-4333-8333-333333333333"
    scene_id = "44444444-4444-4444-8444-444444444444"
    db = DB()
    db.tables["video_project_scenes"] = [{
        "id": scene_id,
        "project_id": project_id,
        "owner_id": "another-user",
        "narration_audio_path": "another-user/project/scene.mp3",
    }]
    response = make_client(db).get(
        f"/video/generations/projects/{project_id}/scenes/{scene_id}/narration"
    )
    assert response.status_code == 404


def test_scene_narration_accepts_eleven_v3_maximum_length(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    project_id = "33333333-3333-4333-8333-333333333333"
    scene_id = "44444444-4444-4444-8444-444444444444"
    db = DB()
    db.tables["video_projects"] = [{"id": project_id, "owner_id": "user-1"}]
    db.tables["video_project_scenes"] = [{
        "id": scene_id,
        "project_id": project_id,
        "owner_id": "user-1",
        "narration": "a" * 5001,
    }]
    response = make_client(db).post(
        f"/video/generations/projects/{project_id}/scenes/{scene_id}/narration",
        json={"voice_id": "voice-test"},
    )
    assert response.status_code == 422
    assert "5,000 characters" in response.json()["detail"]


def test_final_assembly_requires_every_scene_completed(monkeypatch):
    project_id = "33333333-3333-4333-8333-333333333333"
    db = DB()
    db.tables["video_projects"] = [{
        "id": project_id, "owner_id": "user-1", "status": "ready",
        "final_video_path": None,
    }]
    db.tables["video_project_scenes"] = [{
        "id": "44444444-4444-4444-8444-444444444444",
        "project_id": project_id, "owner_id": "user-1",
        "status": "generating", "video_url": None,
    }]
    response = make_client(db).post(f"/video/generations/projects/{project_id}/assemble")
    assert response.status_code == 409
    assert "All scenes must finish" in response.json()["detail"]


def test_final_assembly_queues_only_owner_completed_project(monkeypatch):
    project_id = "33333333-3333-4333-8333-333333333333"
    db = DB()
    db.tables["video_projects"] = [{
        "id": project_id, "owner_id": "user-1", "status": "ready",
        "final_video_path": None,
    }]
    db.tables["video_project_scenes"] = [{
        "id": "44444444-4444-4444-8444-444444444444",
        "project_id": project_id, "owner_id": "user-1",
        "status": "completed", "video_url": "https://example.test/scene.mp4",
    }]
    response = make_client(db).post(f"/video/generations/projects/{project_id}/assemble")
    assert response.status_code == 202
    assert response.json()["status"] == "queued"
    assert len(db.tables["video_assembly_jobs"]) == 1
    job = db.tables["video_assembly_jobs"][0]
    assert job["project_id"] == project_id
    assert job["owner_id"] == "user-1"
    assert job["status"] == "queued"
    assert db.tables["video_projects"][0]["status"] == "assembling"


def test_final_video_url_is_owner_scoped():
    project_id = "33333333-3333-4333-8333-333333333333"
    db = DB()
    db.tables["video_projects"] = [{
        "id": project_id, "owner_id": "another-user",
        "status": "completed", "final_video_path": "another-user/final.mp4",
    }]
    response = make_client(db).get(f"/video/generations/projects/{project_id}/final-video")
    assert response.status_code == 404


def test_video_project_list_returns_only_authenticated_owner_projects():
    db = DB()
    db.tables["video_projects"] = [
        {"id": "33333333-3333-4333-8333-333333333333", "owner_id": "user-1", "title": "My project", "selected_scene_ids": []},
        {"id": "66666666-6666-4666-8666-666666666666", "owner_id": "another-user", "title": "Private project", "selected_scene_ids": []},
    ]
    response = make_client(db).get("/video/generations/projects/list")
    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert [project["title"] for project in payload["projects"]] == ["My project"]


def test_video_project_selection_is_persisted_and_owner_scoped():
    project_id = "33333333-3333-4333-8333-333333333333"
    scene_id = "44444444-4444-4444-8444-444444444444"
    db = DB()
    db.tables["video_projects"] = [{
        "id": project_id, "owner_id": "user-1", "selected_scene_ids": [],
    }]
    db.tables["video_project_scenes"] = [{
        "id": scene_id, "project_id": project_id, "owner_id": "user-1",
    }]
    response = make_client(db).patch(
        f"/video/generations/projects/{project_id}/selection",
        json={"scene_ids": [scene_id]},
    )
    assert response.status_code == 200
    assert response.json()["selected_scene_ids"] == [scene_id]
    assert db.tables["video_projects"][0]["selected_scene_ids"] == [scene_id]


def test_video_project_selection_rejects_scene_from_another_project():
    project_id = "33333333-3333-4333-8333-333333333333"
    scene_id = "44444444-4444-4444-8444-444444444444"
    db = DB()
    db.tables["video_projects"] = [{"id": project_id, "owner_id": "user-1"}]
    db.tables["video_project_scenes"] = [{
        "id": scene_id, "project_id": "66666666-6666-4666-8666-666666666666",
        "owner_id": "user-1",
    }]
    response = make_client(db).patch(
        f"/video/generations/projects/{project_id}/selection",
        json={"scene_ids": [scene_id]},
    )
    assert response.status_code == 404


def test_runway_batch_rejection_is_persisted_and_survives_project_refresh(monkeypatch):
    monkeypatch.setenv("RUNWAYML_API_SECRET", "test-secret")
    project_id = "33333333-3333-4333-8333-333333333333"
    scene_id = "44444444-4444-4444-8444-444444444444"
    class ProjectionCheckingQuery(Query):
        def select(self, *columns):
            if self.table == "video_project_scenes":
                assert columns and "video_url" in columns[0].split(","), (
                    "Runway batch scene query must select video_url"
                )
            return super().select(*columns)

    class ProjectionCheckingDB(DB):
        def table(self, name):
            return ProjectionCheckingQuery(self.tables, name)

    db = ProjectionCheckingDB()
    db.tables["video_projects"] = [{
        "id": project_id, "owner_id": "user-1", "aspect_ratio": "16:9", "status": "ready",
    }]
    db.tables["video_project_scenes"] = [{
        "id": scene_id, "project_id": project_id, "owner_id": "user-1",
        "scene_number": 1, "visual_prompt": "A cinematic scene", "duration_seconds": 5,
        "status": "pending", "generation_job_id": None,
    }]

    class ProviderResponse:
        status_code = 400
        text = '{"error":"invalid_prompt","message":"Prompt violates provider requirements"}'

        def json(self):
            return {"error": "invalid_prompt", "message": "Prompt violates provider requirements"}

    class AsyncClient:
        def __init__(self, **_kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_args): return False
        async def get(self, *_args, **_kwargs):
            class CreditResponse:
                status_code = 200
                def json(self): return {"creditBalance": 10000}
            return CreditResponse()
        async def post(self, *_args, **_kwargs): return ProviderResponse()

    monkeypatch.setattr(video_generation.httpx, "AsyncClient", AsyncClient)
    response = make_client(db).post(
        f"/video/generations/projects/{project_id}/generate-batch",
        json={"scene_ids": [scene_id]},
    )
    assert response.status_code == 202
    result = response.json()["results"][0]
    assert result["status"] == "provider_rejected"
    assert result["http_status"] == 400

    refreshed = make_client(db).get(f"/video/generations/projects/{project_id}")
    assert refreshed.status_code == 200
    scene = refreshed.json()["scenes"][0]
    assert scene["status"] == "failed"
    assert scene["http_status"] == 400
    assert scene["provider_error"]["error"] == "invalid_prompt"
    assert "Prompt violates provider requirements" in scene["provider_error"]["message"]


def test_runway_batch_transport_error_is_persisted(monkeypatch):
    monkeypatch.setenv("RUNWAYML_API_SECRET", "test-secret")
    project_id = "55555555-5555-4555-8555-555555555555"
    scene_id = "66666666-6666-4666-8666-666666666666"
    db = DB()
    db.tables["video_projects"] = [{
        "id": project_id, "owner_id": "user-1", "aspect_ratio": "16:9", "status": "ready",
    }]
    db.tables["video_project_scenes"] = [{
        "id": scene_id, "project_id": project_id, "owner_id": "user-1",
        "scene_number": 2, "visual_prompt": "A cinematic scene", "duration_seconds": 5,
        "status": "pending", "generation_job_id": None,
    }]

    class AsyncClient:
        def __init__(self, **_kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_args): return False
        async def get(self, *_args, **_kwargs):
            class CreditResponse:
                status_code = 200
                def json(self): return {"creditBalance": 10000}
            return CreditResponse()
        async def post(self, *_args, **_kwargs):
            raise video_generation.httpx.ConnectError("connection refused")

    monkeypatch.setattr(video_generation.httpx, "AsyncClient", AsyncClient)
    response = make_client(db).post(
        f"/video/generations/projects/{project_id}/generate-batch",
        json={"scene_ids": [scene_id]},
    )
    assert response.status_code == 202
    assert response.json()["results"][0]["status"] == "provider_error"

    refreshed = make_client(db).get(f"/video/generations/projects/{project_id}")
    assert refreshed.status_code == 200
    scene = refreshed.json()["scenes"][0]
    assert scene["status"] == "failed"
    assert scene["http_status"] is None
    assert scene["provider_error"]["error"] == "provider_transport_error"


def test_runway_batch_missing_task_id_is_persisted(monkeypatch):
    monkeypatch.setenv("RUNWAYML_API_SECRET", "test-secret")
    project_id = "77777777-7777-4777-8777-777777777777"
    scene_id = "88888888-8888-4888-8888-888888888888"
    db = DB()
    db.tables["video_projects"] = [{
        "id": project_id, "owner_id": "user-1", "aspect_ratio": "16:9", "status": "ready",
    }]
    db.tables["video_project_scenes"] = [{
        "id": scene_id, "project_id": project_id, "owner_id": "user-1",
        "scene_number": 3, "visual_prompt": "A cinematic scene", "duration_seconds": 5,
        "status": "pending", "generation_job_id": None,
    }]

    class ProviderResponse:
        status_code = 200
        text = '{"status":"PENDING"}'
        def json(self): return {"status": "PENDING"}

    class AsyncClient:
        def __init__(self, **_kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_args): return False
        async def get(self, *_args, **_kwargs):
            class CreditResponse:
                status_code = 200
                def json(self): return {"creditBalance": 10000}
            return CreditResponse()
        async def post(self, *_args, **_kwargs): return ProviderResponse()

    monkeypatch.setattr(video_generation.httpx, "AsyncClient", AsyncClient)
    response = make_client(db).post(
        f"/video/generations/projects/{project_id}/generate-batch",
        json={"scene_ids": [scene_id]},
    )
    assert response.status_code == 202
    assert response.json()["results"][0]["status"] == "missing_task_id"

    refreshed = make_client(db).get(f"/video/generations/projects/{project_id}")
    assert refreshed.status_code == 200
    scene = refreshed.json()["scenes"][0]
    assert scene["status"] == "failed"
    assert scene["http_status"] == 200
    assert scene["provider_error"]["error"] == "missing_task_id"



def test_runway_credit_estimator_uses_model_rate_and_duration():
    assert video_generation._estimate_runway_credits("gen4.5", [5, 10]) == 180
    assert video_generation._estimate_runway_credits("gen4_turbo", [5, 10]) == 75


def test_runway_batch_blocks_before_any_paid_request_when_credits_insufficient(monkeypatch):
    monkeypatch.setenv("RUNWAYML_API_SECRET", "test-secret")
    project_id = "99999999-9999-4999-8999-999999999999"
    scene_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    db = DB()
    db.tables["video_projects"] = [{"id": project_id, "owner_id": "user-1", "aspect_ratio": "16:9", "status": "ready"}]
    db.tables["video_project_scenes"] = [{
        "id": scene_id, "project_id": project_id, "owner_id": "user-1",
        "scene_number": 1, "visual_prompt": "A cinematic scene", "duration_seconds": 5,
        "status": "pending", "generation_job_id": None,
    }]
    calls = {"get": 0, "post": 0}
    class CreditResponse:
        status_code = 200
        def json(self): return {"creditBalance": 40}
    class AsyncClient:
        def __init__(self, **_kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_args): return False
        async def get(self, *_args, **_kwargs):
            calls["get"] += 1
            return CreditResponse()
        async def post(self, *_args, **_kwargs):
            calls["post"] += 1
            raise AssertionError("Paid generation must not be called after failed preflight")
    monkeypatch.setattr(video_generation.httpx, "AsyncClient", AsyncClient)
    response = make_client(db).post(f"/video/generations/projects/{project_id}/generate-batch", json={"scene_ids": [scene_id]})
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "insufficient_credits"
    assert detail["available_credits"] == 40
    assert detail["estimated_credits"] == 60
    assert detail["shortfall_credits"] == 20
    assert calls == {"get": 1, "post": 0}
    assert db.tables["video_project_scenes"][0]["status"] == "pending"


def test_runway_batch_preflight_uses_organization_credit_balance(monkeypatch):
    monkeypatch.setenv("RUNWAYML_API_SECRET", "test-secret")
    project_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    scene_id = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
    db = DB()
    db.tables["video_projects"] = [{"id": project_id, "owner_id": "user-1", "aspect_ratio": "16:9", "status": "ready"}]
    db.tables["video_project_scenes"] = [{
        "id": scene_id, "project_id": project_id, "owner_id": "user-1",
        "scene_number": 1, "visual_prompt": "A cinematic scene", "duration_seconds": 5,
        "status": "pending", "generation_job_id": None,
    }]
    class CreditResponse:
        status_code = 200
        def json(self): return {"creditBalance": 50}
    class AsyncClient:
        def __init__(self, **_kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_args): return False
        async def get(self, *_args, **_kwargs): return CreditResponse()
        async def post(self, *_args, **_kwargs): raise AssertionError("Must use available balance")
    monkeypatch.setattr(video_generation.httpx, "AsyncClient", AsyncClient)
    response = make_client(db).post(f"/video/generations/projects/{project_id}/generate-batch", json={"scene_ids": [scene_id]})
    assert response.status_code == 409
    assert response.json()["detail"]["available_credits"] == 50



def test_runway_batch_uses_user_selected_gen4_turbo_model(monkeypatch):
    monkeypatch.setenv("RUNWAYML_API_SECRET", "test-secret")
    project_id = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
    scene_id = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
    db = DB()
    db.tables["video_projects"] = [{"id": project_id, "owner_id": "user-1", "aspect_ratio": "16:9", "status": "ready"}]
    db.tables["video_project_scenes"] = [{
        "id": scene_id, "project_id": project_id, "owner_id": "user-1",
        "scene_number": 1, "visual_prompt": "A cinematic scene", "duration_seconds": 5,
        "status": "pending", "generation_job_id": None,
    }]
    submitted = []
    class CreditResponse:
        status_code = 200
        def json(self): return {"creditBalance": 100}
    class ProviderResponse:
        status_code = 200
        def json(self): return {"id": "runway-task-turbo", "status": "PENDING"}
    class AsyncClient:
        def __init__(self, **_kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_args): return False
        async def get(self, *_args, **_kwargs): return CreditResponse()
        async def post(self, _url, **kwargs):
            submitted.append(kwargs["json"])
            return ProviderResponse()
    monkeypatch.setattr(video_generation.httpx, "AsyncClient", AsyncClient)
    response = make_client(db).post(
        f"/video/generations/projects/{project_id}/generate-batch",
        json={"scene_ids": [scene_id], "model": "gen4_turbo"},
    )
    assert response.status_code == 202
    assert submitted[0]["model"] == "gen4_turbo"
    assert response.json()["results"][0]["status"] == "pending"
    assert db.tables["video_generation_jobs"][0]["model"] == "gen4_turbo"


def test_runway_credit_preflight_reads_organization_endpoint_and_credit_balance(monkeypatch):
    requested = []

    class OrganizationResponse:
        status_code = 200
        def json(self):
            return {"creditBalance": 1040}

    class AsyncClient:
        def __init__(self, **_kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_args): return False
        async def get(self, url, **kwargs):
            requested.append((url, kwargs))
            return OrganizationResponse()

    monkeypatch.setattr(video_generation.httpx, "AsyncClient", AsyncClient)
    balance = __import__("asyncio").run(
        video_generation._runway_available_credits({"Authorization": "Bearer test-secret"})
    )

    assert balance == 1040
    assert requested[0][0] == "https://api.dev.runwayml.com/v1/organization"
    assert requested[0][1]["headers"]["Authorization"] == "Bearer test-secret"


def test_runway_credit_preflight_blocks_unknown_organization_balance(monkeypatch):
    class OrganizationResponse:
        status_code = 200
        def json(self):
            return {"unexpected": 1040}

    class AsyncClient:
        def __init__(self, **_kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_args): return False
        async def get(self, *_args, **_kwargs): return OrganizationResponse()

    monkeypatch.setattr(video_generation.httpx, "AsyncClient", AsyncClient)
    import asyncio
    try:
        asyncio.run(video_generation._runway_available_credits({"Authorization": "Bearer test-secret"}))
        assert False, "Unknown balance must block generation"
    except video_generation.HTTPException as exc:
        assert exc.status_code == 503
        assert "generation was not started" in exc.detail


def test_runway_provider_credits_endpoint_is_read_only(monkeypatch):
    calls = {"credits": 0}
    monkeypatch.setattr(video_generation, "_headers", lambda: {"Authorization": "Bearer test-secret"})

    async def read_balance(_headers):
        calls["credits"] += 1
        return 1040

    monkeypatch.setattr(video_generation, "_runway_available_credits", read_balance)
    import asyncio
    result = asyncio.run(video_generation.get_runway_provider_credits(owner_id="test-user"))

    assert result == {
        "success": True,
        "provider": "runway",
        "connected": True,
        "credit_balance": 1040,
        "generation_started": False,
    }
    assert calls["credits"] == 1

def test_runway_batch_skips_scene_with_saved_video_url_even_if_status_is_stale(monkeypatch):
    monkeypatch.setenv("RUNWAYML_API_SECRET", "test-secret")
    project_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    scene_id = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
    db = DB()
    db.tables["video_projects"] = [{
        "id": project_id, "owner_id": "user-1", "aspect_ratio": "16:9", "status": "ready",
    }]
    db.tables["video_project_scenes"] = [{
        "id": scene_id, "project_id": project_id, "owner_id": "user-1",
        "scene_number": 8, "visual_prompt": "Already generated scene",
        "duration_seconds": 5, "status": "failed", "generation_job_id": None,
        "video_url": "https://assets.example.test/scene-8.mp4",
    }]

    class AsyncClient:
        def __init__(self, **_kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_args): return False
        async def get(self, *_args, **_kwargs):
            raise AssertionError("Saved scene must be skipped before credit lookup")
        async def post(self, *_args, **_kwargs):
            raise AssertionError("Saved scene must never trigger paid generation")

    monkeypatch.setattr(video_generation.httpx, "AsyncClient", AsyncClient)
    response = make_client(db).post(
        f"/video/generations/projects/{project_id}/generate-batch",
        json={"scene_ids": [scene_id], "model": "gen4_turbo"},
    )
    assert response.status_code == 202
    payload = response.json()
    assert payload["success"] is True
    assert payload["started"] == 0
    assert payload["results"][0]["status"] == "skipped_completed"
    assert db.tables["video_project_scenes"][0]["video_url"] == "https://assets.example.test/scene-8.mp4"
