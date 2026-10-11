from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import psycopg2
import pytest


ROOT = Path(__file__).resolve().parents[1]
DSN = os.getenv("TEST_POSTGRES_DSN")


def _connect():
    conn = psycopg2.connect(DSN)
    conn.autocommit = False
    return conn


@pytest.fixture(scope="module", autouse=True)
def isolated_postgres_schema():
    if not DSN:
        pytest.skip("TEST_POSTGRES_DSN is not configured; live Postgres concurrency test only runs in its isolated CI job")

    conn = _connect()
    try:
        database = conn.get_dsn_parameters().get("dbname")
        if database != "aea_claim_test":
            pytest.fail("Refusing to reset database unless dbname is exactly 'aea_claim_test'")

        with conn.cursor() as cur:
            cur.execute("DROP TABLE IF EXISTS public.mission_steps, public.mission_executions CASCADE")
            cur.execute(
                """
                CREATE TABLE public.mission_executions (
                    id UUID PRIMARY KEY,
                    mission_id UUID NOT NULL,
                    execution_id UUID NOT NULL UNIQUE
                )
                """
            )
            cur.execute(
                """
                CREATE TABLE public.mission_steps (
                    id UUID PRIMARY KEY,
                    mission_id UUID,
                    execution_id UUID,
                    step_name TEXT NOT NULL,
                    worker_role TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempt_index INTEGER NOT NULL DEFAULT 0,
                    idempotency_key TEXT UNIQUE,
                    operation_key TEXT,
                    claim_token TEXT,
                    lease_expires_at TIMESTAMPTZ,
                    started_at TIMESTAMPTZ,
                    completed_at TIMESTAMPTZ,
                    result JSONB NOT NULL DEFAULT '{}'::jsonb,
                    retry_category TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            migration = (ROOT / "supabase/migrations/000000000017_atomic_mission_step_claim.sql").read_text()
            cur.execute(migration)
        conn.commit()
    finally:
        conn.close()

    yield

    conn = _connect()
    try:
        if conn.get_dsn_parameters().get("dbname") == "aea_claim_test":
            with conn.cursor() as cur:
                cur.execute("DROP TABLE IF EXISTS public.mission_steps, public.mission_executions CASCADE")
            conn.commit()
    finally:
        conn.close()


def _claim(execution_id: str, operation_key: str, attempt: int, lease_seconds: int = 60):
    step_id = str(uuid4())
    idempotency_key = f"{operation_key}:attempt:{attempt}"
    token = f"token:{uuid4()}"
    conn = _connect()
    try:
        with conn.cursor() as cur:
            # Match the production lifecycle: create a pending step row first;
            # the RPC claims that exact row instead of inserting a replacement.
            cur.execute(
                """
                INSERT INTO public.mission_steps
                    (id, mission_id, execution_id, step_name, worker_role, status, attempt_index, result)
                SELECT %s, mission_id, NULL, %s, 'employee', 'pending', %s, '{}'::jsonb
                FROM public.mission_executions WHERE execution_id = %s
                """,
                (step_id, f"step-{attempt}", attempt, execution_id),
            )
            assert cur.rowcount == 1
            cur.execute(
                """
                SELECT step, claimed
                FROM public.claim_mission_step(%s, %s, %s, %s, %s, %s, %s)
                """,
                (execution_id, step_id, attempt, idempotency_key, operation_key, token, lease_seconds),
            )
            row = cur.fetchone()
        conn.commit()
        step = row[0]
        if isinstance(step, str):
            step = json.loads(step)
        return {"step": step, "claimed": bool(row[1]), "token": token}
    finally:
        conn.close()


def _create_execution():
    mission_id, execution_id = str(uuid4()), str(uuid4())
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO public.mission_executions (id, mission_id, execution_id) VALUES (%s, %s, %s)",
                (str(uuid4()), mission_id, execution_id),
            )
        conn.commit()
    finally:
        conn.close()
    return execution_id


def test_two_independent_postgres_connections_only_one_claim_wins():
    # This runs against actual PostgreSQL with independent DB connections,
    # not the application's process-local locks or an in-memory fake.
    for _ in range(100):
        execution_id = _create_execution()
        operation_key = f"concurrent:{uuid4()}"
        barrier = threading.Barrier(2)

        def worker(attempt: int):
            barrier.wait(timeout=5)
            return _claim(execution_id, operation_key, attempt)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(worker, (0, 1)))

        assert sum(result["claimed"] for result in results) == 1
        assert results[0]["step"]["id"] == results[1]["step"]["id"]

        conn = _connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT count(*) FROM public.mission_steps WHERE execution_id=%s AND operation_key=%s AND status='in_progress'",
                    (execution_id, operation_key),
                )
                assert cur.fetchone()[0] == 1
            conn.rollback()
        finally:
            conn.close()


def test_expired_claim_is_recovered_and_old_token_is_fenced():
    execution_id = _create_execution()
    operation_key = f"stale:{uuid4()}"
    first = _claim(execution_id, operation_key, 0, lease_seconds=1)
    assert first["claimed"] is True
    time.sleep(1.2)

    second = _claim(execution_id, operation_key, 1, lease_seconds=60)
    assert second["claimed"] is True
    assert second["step"]["id"] != first["step"]["id"]

    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status, claim_token FROM public.mission_steps WHERE id=%s",
                (first["step"]["id"],),
            )
            old_status, old_token = cur.fetchone()
            assert old_status == "failed"
            assert old_token is None

            cur.execute(
                "UPDATE public.mission_steps SET status='completed' WHERE id=%s AND claim_token=%s",
                (first["step"]["id"], first["token"]),
            )
            assert cur.rowcount == 0
        conn.rollback()
    finally:
        conn.close()

    replay = _claim(execution_id, operation_key, 2)
    assert replay["claimed"] is False
    assert replay["step"]["id"] == second["step"]["id"]
