"""Bilingual intent and actual Studio orchestration with a simulated provider/database."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.dependencies import get_current_user_id, get_user_scoped_client
from app.routers import content_studio, employee as employee_router
from app.services.agent_runtime import AEAAgentRuntime
from app.services.agent_runtime_contract import RuntimeRequest
from app.services.employee_vertical_slice import EmployeeVerticalSlice
from app.services.mission_execution_service import MissionExecutionService
from app.services.mission_orchestration import MissionOrchestrationService
from app.services.studio_objective import interpret_studio_goal


@pytest.mark.parametrize(("instruction", "kind", "topic", "language"), [
    ("Create an e-book about business opportunities in Tanzania.", "ebook", "business opportunities in Tanzania", "English"),
    ("Could you write me a full ebook covering home budgeting?", "ebook", "home budgeting", "English"),
    ("Generate a course on bookkeeping", "course", "bookkeeping", "English"),
    ("Draft a newsletter regarding local tourism", "newsletter", "local tourism", "English"),
    ("Write an article: safer bicycle commuting", "article", "safer bicycle commuting", "English"),
    ("Please make a workbook about stocktaking", "workbook", "stocktaking", "English"),
    ("Compose a social media post about the open day", "social_post", "the open day", "English"),
    ("Draft a blog post about grocery shopping in Swahili", "blog", "grocery shopping", "Swahili"),
    ("I want to create a short course about invoice preparation.", "course", "invoice preparation", "English"),
    ("Create a complete e book on phone photography", "ebook", "phone photography", "English"),
    ("Tengeneza e-book kuhusu fursa za biashara Tanzania", "ebook", "fursa za biashara Tanzania", "Swahili"),
    ("Niandikie kitabu cha kielektroniki kuhusu kilimo cha mjini", "ebook", "kilimo cha mjini", "Swahili"),
    ("Tafadhali andika makala kuhusu ufugaji wa kuku.", "article", "ufugaji wa kuku", "Swahili"),
    ("Unda kozi juu ya utunzaji wa kumbukumbu", "course", "utunzaji wa kumbukumbu", "Swahili"),
    ("Niundie kitabu cha mazoezi kuhusu hesabu za mauzo", "workbook", "hesabu za mauzo", "Swahili"),
    ("Andaa jarida kuhusu masoko ya ndani", "newsletter", "masoko ya ndani", "Swahili"),
    ("Niandalie blogu kuhusu usafiri", "blog", "usafiri", "Swahili"),
    ("Nitengenezee chapisho la mitandao ya kijamii kuhusu maonyesho", "social_post", "maonyesho", "Swahili"),
    ("Andika makala kuhusu utalii kwa Kiingereza", "article", "utalii", "English"),
    ("Andika rasimu ya kitabu cha kidijitali kuhusu biashara", "ebook", "biashara", "Swahili"),
    ("Create an ebook", "ebook", None, "English"),
    ("Tengeneza kitabu kuhusu", "ebook", None, "Swahili"),
])
def test_bilingual_intent_keeps_the_topic(instruction, kind, topic, language):
    assert interpret_studio_goal(instruction, {}, "en") == {
        "intent": "create_studio_draft", "content_type": kind, "topic": topic, "language": language,
    }


@pytest.fixture(autouse=True)
def isolate_in_memory_stores(monkeypatch):
    for field in ("_memory_missions", "_objective_keys"):
        monkeypatch.setattr(MissionOrchestrationService, field, {})
    monkeypatch.setattr(MissionOrchestrationService, "_memory_events", [])


class DraftDatabase:
    """An owner filter is required on read, even when the adapter returns a row."""

    def __init__(self):
        self.rows = []
        self.corrupt_readback = False
        self.executions = {}
        self.steps = {}

    def table(self, name):
        assert name == "content_asset_versions"
        return DraftQuery(self)


class DraftQuery:
    def __init__(self, database):
        self.database = database
        self.filters = {}
        self.new_row = None

    def select(self, _columns):
        return self

    def limit(self, _limit):
        return self

    def order(self, _key, *, desc):
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def insert(self, payload):
        self.new_row = dict(payload)
        return self

    def execute(self):
        if self.new_row:
            self.database.rows.append(self.new_row)
            return SimpleNamespace(data=[self.new_row])
        assert "owner_id" in self.filters
        rows = [dict(row) for row in self.database.rows if all(row[key] == val for key, val in self.filters.items())]
        if self.database.corrupt_readback and "asset_id" in self.filters and rows:
            rows[0]["content"] = {"title": "Different draft"}
        return SimpleNamespace(data=rows)


class StudioProvider:
    def __init__(self, *, fail_first=False):
        self.prompts = []
        self.fail_first = fail_first

    def generate(self, *, prompt, system_prompt):
        self.prompts.append(json.loads(prompt))
        if self.fail_first and len(self.prompts) == 1:
            return {"success": False}
        topics = (
            "Select a suitable business concept and observe the customers' purchasing habits.",
            "Estimate operating expenses and track each expense in a written daily record.",
            "Review the results at the end of the week and adjust the costs based on demand.",
        )
        chapters = [
            {"title": f"Chapter {index}", "body": (topic + " ") * 15}
            for index, topic in enumerate(topics, start=1)
        ]
        return {
            "success": True,
            "content": json.dumps({
                "title": "Business opportunities in Tanzania",
                "core_content": {"chapters": chapters},
                "hooks": ["Learn how to test an idea"],
                "caption": "Plan a small business",
                "cta": "Read the book",
                "hashtags": ["#Business"],
            }),
        }


@pytest.fixture
def studio(monkeypatch):
    database = DraftDatabase()
    provider = StudioProvider()
    monkeypatch.setattr(content_studio, "get_llm_provider", lambda: provider)
    return database, provider


def make_employee(owner, database, *, max_decisions=3):
    execution = MissionExecutionService(client=None)
    execution._memory_executions = database.executions
    execution._memory_steps = database.steps
    return EmployeeVerticalSlice(
        owner, client=database,
        execution_service=execution,
        max_decisions=max_decisions,
    )


def test_authenticated_objective_runs_studio_and_replays_the_saved_result(studio, monkeypatch):
    database, provider = studio
    api = FastAPI()
    api.include_router(employee_router.router)
    client = TestClient(api)
    assert client.post("/employee/objective", json={"objective": "Write an e-book on business"}).status_code == 401
    services = {}

    def orchestration(owner, _client):
        return services.setdefault(owner, MissionOrchestrationService(owner, employee=make_employee(owner, database)))

    monkeypatch.setattr(employee_router, "_service", orchestration)
    monkeypatch.setattr(employee_router, "AEAAgentRuntime", lambda owner, client: AEAAgentRuntime(
        owner, orchestration=orchestration(owner, client),
    ))
    api.dependency_overrides[get_current_user_id] = lambda: "creator-a"
    api.dependency_overrides[get_user_scoped_client] = lambda: database
    body = {
        "objective": "Tengeneza e-book kuhusu fursa za biashara Tanzania",
        "run_now": True,
        "idempotency_key": "test-studio-objective-1",
        "locale": "sw-TZ",
    }

    first = client.post("/employee/objective", json=body)
    assert first.status_code == 201, first.text
    payload = first.json()
    assert payload["mission"]["status"] == "completed"
    assert payload["runtime"]["status"] == "SUCCEEDED"
    report = payload["runtime"]["output"]["report"]
    assert report["outcome"] == "VERIFIED"
    assert report["selected_tools"] == ["studio.generate_draft", "studio.save_draft", "studio.verify_draft"]
    assert report["validated_plan"][-1]["depends_on"] == ["save_studio_draft"]
    assert report["deliverable"]["asset_id"] == database.rows[0]["asset_id"]
    assert report["deliverable"]["verification"] == "OWNER_SCOPED_STUDIO_READBACK"
    assert provider.prompts[0]["language"] == "Swahili"
    assert len(database.rows[0]["content"]["core_content"]["chapters"]) == 3

    replay = client.post("/employee/objective", json=body).json()
    assert replay["mission"]["id"] == payload["mission"]["id"]
    assert replay["runtime"]["evidence"]["idempotent"] is True
    assert replay["runtime"]["output"]["report"]["deliverable"] == report["deliverable"]
    assert len(provider.prompts) == len(database.rows) == 1

    changed = client.post("/employee/objective", json={**body, "objective": "Create an e-book about gardening"})
    assert changed.status_code == 400
    api.dependency_overrides[get_current_user_id] = lambda: "creator-b"
    asset_id = report["deliverable"]["asset_id"]
    assert client.get(f"/employee/missions/{payload['mission']['id']}").status_code == 404
    assert content_studio.list_studio_asset_versions(asset_id, "creator-b", database)["versions"] == []


def test_known_missing_topic_is_the_only_requested_input(studio):
    database, provider = studio
    employee = make_employee("creator", database)
    result = employee.run("Tengeneza kitabu")
    assert result["status"] == "WAIT_FOR_HUMAN_INPUT"
    assert result["report"]["missing_inputs"] == ["topic"]
    assert result["report"]["required_user_action"] == "Maudhui yawe kuhusu nini?"
    assert not provider.prompts
    clarified = employee.run("Tengeneza kitabu", metadata={"content_inputs": {"topic": "Ujasiriamali"}})
    assert clarified["status"] == "COMPLETE"
    assert provider.prompts[0]["niche"] == "Ujasiriamali"


@pytest.mark.parametrize("goal", [
    "Prepare my e-book for sale",
    "Create an ebook about businesses and publish it",
    "Endelea na video yangu",
    "Promote my existing product",
    "Design a promotional poster for my business",
    "Generate a course on stock control and an ebook about expenses",
    "Tengeneza kitabu kuhusu biashara halafu ichapishe",
])
def test_unavailable_workflows_do_not_claim_execution_or_create_side_effects(studio, goal):
    database, provider = studio
    result = make_employee("creator", database).run(goal)
    assert result["success"] is False
    assert result["status"] == "FAIL"
    assert not result["report"]["executed_actions"]
    assert "unavailable" in result["report"]["failures_retries"][0].lower() or not result["report"]["plan"]
    assert not database.rows and not provider.prompts


def test_failed_generation_recovers_with_fresh_output_after_restart(studio):
    database, provider = studio
    provider.fail_first = True
    mission_id = str(uuid4())
    goal = "Create an e-book about business opportunities in Tanzania"
    first = make_employee("creator", database).run(goal, mission_id=mission_id)
    assert first["status"] == "RETRY"
    assert first["report"]["execution_evidence"][0]["success"] is False
    assert database.rows == []
    resumed = make_employee("creator", database).run(goal, mission_id=mission_id)
    assert resumed["report"]["outcome"] == "VERIFIED"
    assert resumed["report"]["execution_id"] == first["report"]["execution_id"]
    assert len(provider.prompts) == 2
    assert len(database.rows) == 1


def test_resume_skips_saved_steps_and_refuses_a_mismatched_readback(studio):
    database, provider = studio
    database.corrupt_readback = True
    mission_id = str(uuid4())
    goal = "Create an e-book about business opportunities in Tanzania"
    first = make_employee("creator", database).run(goal, mission_id=mission_id)
    assert first["status"] == "RETRY"
    assert [entry["outcome_status"] for entry in first["report"]["execution_evidence"]] == ["generated", "saved", None]
    assert "outcome" not in first["report"]
    database.corrupt_readback = False
    resumed = make_employee("creator", database).run(goal, mission_id=mission_id)
    assert resumed["report"]["outcome"] == "VERIFIED"
    assert len(provider.prompts) == len(database.rows) == 1


def test_report_marks_saved_but_unverified_draft_as_partial(studio):
    database, provider = studio
    database.corrupt_readback = True
    runner = make_employee("creator", database, max_decisions=0)
    orchestration = MissionOrchestrationService("creator", employee=runner)
    runtime = AEAAgentRuntime("creator", orchestration=orchestration)
    result = runtime.submit(RuntimeRequest("creator", "Write an e-book about business"), run_now=True)
    assert result["status"] == "PARTIAL"
    report = result["output"]["report"]
    assert report["final_status"] == "FAIL"
    assert report["deliverable"]["status"] == "saved"
    assert len(provider.prompts) == len(database.rows) == 1


def test_interrupted_active_mission_is_recoverable_only_after_lease_expires(studio):
    database, _provider = studio
    orchestration = MissionOrchestrationService("creator", employee=make_employee("creator", database))
    mission = orchestration.create_mission("Write an e-book about business")["mission"]
    mission_id = mission["id"]
    assert orchestration._claim_mission(mission, "interrupted-worker")
    orchestration.transition(mission_id, "active")
    assert orchestration.run_mission(mission_id)["status"] == "WAITING"
    orchestration._update_mission(mission_id, {
        "orchestration_lease_expires_at": (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(),
    })
    assert orchestration.run_mission(mission_id)["mission"]["status"] == "completed"


def test_forward_dependencies_are_rejected():
    employee = EmployeeVerticalSlice("creator")
    result = employee._validator.validate([
        {"step_name": "save", "tool_name": "studio.save_draft", "input": {}, "depends_on": ["generate"]},
        {"step_name": "generate", "tool_name": "log", "input": {"message": "example"}},
    ], owner_id="creator", discovered=employee.discover_capabilities())
    assert result["success"] is False
    assert "dependencies must name earlier steps" in result["errors"][0]
