from types import SimpleNamespace

import pytest

from app.services.freelancer_projects import (
    FreelancerProjectError, create_project, list_projects, update_project,
)


class FakeQuery:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.payload = None

    def insert(self, payload):
        self.payload = payload
        return self

    def select(self, *_args):
        return self

    def update(self, payload):
        self.payload = payload
        return self

    def eq(self, *_args):
        return self

    def order(self, *_args, **_kwargs):
        return self

    def limit(self, *_args):
        return self

    def execute(self):
        return SimpleNamespace(data=[{**(self.payload or {}), "id": "project-1"}] if self.payload else self.rows)


class FakeClient:
    def __init__(self):
        self.query = FakeQuery()

    def table(self, _name):
        return self.query


def test_create_project_persists_owner_and_distinguishes_received_amount():
    client = FakeClient()
    result = create_project(client, "user-1", {
        "title": "SEO writing",
        "job_description": "Write a short SEO article",
        "agreed_amount": 100,
        "amount_received": 25,
    })
    assert result["owner_id"] == "user-1"
    assert result["agreed_amount"] == 100
    assert result["amount_received"] == 25


def test_create_project_rejects_received_amount_above_agreed():
    with pytest.raises(FreelancerProjectError, match="cannot exceed"):
        create_project(FakeClient(), "user-1", {
            "title": "SEO writing",
            "job_description": "Write an article",
            "agreed_amount": 100,
            "amount_received": 101,
        })


def test_create_project_rejects_blank_description():
    with pytest.raises(FreelancerProjectError, match="job_description"):
        create_project(FakeClient(), "user-1", {
            "title": "SEO writing",
            "job_description": " ",
        })


def test_list_projects_uses_authenticated_client_query():
    client = FakeClient()
    projects = list_projects(client)
    assert projects == []


def test_update_project_rejects_invalid_status():
    with pytest.raises(FreelancerProjectError, match="Invalid project status"):
        update_project(FakeClient(), "project-1", {"status": "unknown"})
