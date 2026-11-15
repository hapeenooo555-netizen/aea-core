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

    def select(self, *_args): self.action = "select"; return self
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
