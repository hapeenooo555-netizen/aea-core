"""Hub-selected worker/mission linking and first-publish completion."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.dependencies import get_current_user_id, get_user_scoped_client
from app.routers import approvals as approvals_router
from app.routers import chatplus_pinterest as cp

OWNER = "owner-1"
ROWS = {
    "workers": [{"id": "w1", "owner_id": OWNER}, {"id": "w-other", "owner_id": "owner-2"}],
    "missions": [
        {"id": "m1", "owner_id": OWNER, "worker_id": "w1"},
        {"id": "m-wrong-worker", "owner_id": OWNER, "worker_id": "w9"},
        {"id": "m-other", "owner_id": "owner-2", "worker_id": "w-other"},
    ],
}


class Query:
    def __init__(self, table):
        self.rows, self.filters = ROWS.get(table, []), {}

    def select(self, *_):
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def limit(self, _):
        return self

    def execute(self):
        data = [r for r in self.rows if all(r.get(k) == v for k, v in self.filters.items())]
        return type("R", (), {"data": data})()


class Client:
    def table(self, name):
        return Query(name)


created = {}


class FakeOrchestration:
    def __init__(self, *_a, **_k):
        pass

    def create_mission(self, **kwargs):
        created["mission"] = kwargs
        return {"success": True, "mission": {"id": "approval-mission-1"}}


class FakeGateway:
    def __init__(self, *_a, **_k):
        pass

    def list_requests(self, **_k):
        return []

    def create_request(self, **kwargs):
        created["approval"] = kwargs
        return {"success": True, "request": {"id": "appr-1", "mission_id": kwargs["mission_id"], "status": "pending"}}


DRAFT = {"title": "T", "description": "D", "destination_url": "https://shop.example/p", "image_url": "https://img.example/p.jpg", "board_id": "b1"}


def make_client(monkeypatch):
    monkeypatch.setattr(cp, "get_access_token", lambda owner_id: {"access_token": "x"})
    monkeypatch.setattr(cp, "verify_account_and_boards", lambda _t: {"account": {"username": "u"}, "boards": [{"id": "b1", "name": "Ideas"}]})
    monkeypatch.setattr(cp, "MissionOrchestrationService", FakeOrchestration)
    monkeypatch.setattr(cp, "ApprovalGateway", FakeGateway)
    app = FastAPI()
    app.include_router(cp.router)
    app.dependency_overrides[get_current_user_id] = lambda: OWNER
    app.dependency_overrides[get_user_scoped_client] = lambda: Client()
    return TestClient(app)


def test_links_owned_worker_and_mission_and_returns_ids(monkeypatch):
    created.clear()
    res = make_client(monkeypatch).post("/chatplus/pinterest/draft-approval", json={**DRAFT, "worker_id": "w1", "mission_id": "m1"})
    assert res.status_code == 201
    body = res.json()
    assert body["approval_request_id"] == "appr-1"
    assert body["mission_id"] == "approval-mission-1"
    assert body["source_mission_id"] == "m1"
    assert body["published"] is False
    assert created["approval"]["payload"]["source_mission_id"] == "m1"
    assert created["mission"]["metadata"]["source_worker_id"] == "w1"


def test_rejects_another_owners_worker(monkeypatch):
    res = make_client(monkeypatch).post("/chatplus/pinterest/draft-approval", json={**DRAFT, "worker_id": "w-other"})
    assert res.status_code == 404


def test_rejects_another_owners_mission(monkeypatch):
    res = make_client(monkeypatch).post("/chatplus/pinterest/draft-approval", json={**DRAFT, "worker_id": "w1", "mission_id": "m-other"})
    assert res.status_code == 404


def test_rejects_mission_of_a_different_worker(monkeypatch):
    res = make_client(monkeypatch).post("/chatplus/pinterest/draft-approval", json={**DRAFT, "worker_id": "w1", "mission_id": "m-wrong-worker"})
    assert res.status_code == 422


def test_works_without_selection(monkeypatch):
    res = make_client(monkeypatch).post("/chatplus/pinterest/draft-approval", json=DRAFT)
    assert res.status_code == 201
    assert res.json()["source_mission_id"] is None


def _transition_for(monkeypatch, outcome):
    seen = {}

    class Orch:
        def __init__(self, *_a, **_k):
            pass

        def get_mission(self, _id):
            return {"id": "m", "orchestration_idempotency_key": "k", "objective": "o"}

        def transition(self, _id, target, result=None):
            seen["target"] = target

    monkeypatch.setattr(approvals_router, "MissionOrchestrationService", Orch)
    approvals_router._update_p1_8_mission_state({"mission_id": "m"}, outcome, OWNER, None)
    return seen.get("target")


def test_first_publish_with_pin_id_completes_mission(monkeypatch):
    assert _transition_for(monkeypatch, {"status": "resumed", "result": {"pin_id": "123"}}) == "completed"


def test_resumed_without_pin_id_does_not_complete(monkeypatch):
    assert _transition_for(monkeypatch, {"status": "resumed", "result": {}}) is None
