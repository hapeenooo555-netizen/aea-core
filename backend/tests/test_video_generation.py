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
    def insert(self, payload): self.action, self.payload = "insert", payload; return self
    def update(self, payload): self.action, self.payload = "update", payload; return self

    def execute(self):
        rows = self.db.setdefault(self.table, [])
        matches = [r for r in rows if all(r.get(k) == v for k, v in self.filters.items())]
        if self.action == "insert":
            rows.append(dict(self.payload))
            return Response([self.payload])
        if self.action == "update":
            for row in matches: row.update(self.payload)
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
