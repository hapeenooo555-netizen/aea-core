from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services.meta_oauth_state import make_state, verify_state


def test_meta_oauth_state_is_signed_and_owner_bound() -> None:
    state = make_state("owner-a", "state-secret", now=1000)
    data = verify_state(state, "state-secret", now=1001)
    assert data is not None
    assert data["owner_id"] == "owner-a"
    assert data["nonce"]


def test_meta_oauth_state_rejects_tampering() -> None:
    state = make_state("owner-a", "state-secret", now=1000)
    tampered = state[:-1] + ("A" if state[-1] != "A" else "B")
    assert verify_state(tampered, "state-secret", now=1001) is None


def test_meta_oauth_state_expires() -> None:
    state = make_state("owner-a", "state-secret", now=1000)
    assert verify_state(state, "state-secret", now=1901) is None


def test_meta_oauth_state_rejects_wrong_secret() -> None:
    state = make_state("owner-a", "state-secret", now=1000)
    assert verify_state(state, "other-secret", now=1001) is None
