from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

import api_server
from src.api import research_routes
from src.research_workspace.store import ResearchWorkspaceStore
from src.tools import build_registry


@pytest.fixture()
def research_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    store = ResearchWorkspaceStore(tmp_path / "research.db")
    monkeypatch.setattr(research_routes, "_store", store)
    try:
        yield store
    finally:
        store.close()


def _client() -> TestClient:
    return TestClient(api_server.app, client=("127.0.0.1", 50000))


def test_publish_tool_is_available_to_normal_agent_sessions() -> None:
    assert build_registry().get("publish_workspace_research") is not None


def test_refresh_prompt_contains_persistable_run_id(
    research_store: ResearchWorkspaceStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    class FakeService:
        def create_session(self, **_kwargs):
            return SimpleNamespace(session_id="session-refresh")

        async def send_message(self, session_id: str, prompt: str):
            captured.update(session_id=session_id, prompt=prompt)

    monkeypatch.setattr(api_server, "_get_session_service", lambda: FakeService())
    response = _client().post("/dashboard/refresh", json={"module": "macro", "market": "CN"})

    assert response.status_code == 200
    run = response.json()
    assert run["status"] == "queued"
    assert run["linked_run_id"] == "session-refresh"
    assert f"run_id={run['id']}" in captured["prompt"]
    assert "publish_workspace_research" in captured["prompt"]


def test_refresh_delivery_failure_updates_same_run(
    research_store: ResearchWorkspaceStore,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeService:
        def create_session(self, **_kwargs):
            return SimpleNamespace(session_id="session-failed")

        async def send_message(self, _session_id: str, _prompt: str):
            raise RuntimeError("queue unavailable")

    monkeypatch.setattr(api_server, "_get_session_service", lambda: FakeService())
    response = _client().post("/dashboard/refresh", json={"module": "macro", "market": "US"})

    assert response.status_code == 200
    run = response.json()
    assert run["status"] == "failed"
    assert "queue unavailable" in run["message"]
    count = research_store._conn.execute("SELECT COUNT(*) FROM research_runs").fetchone()[0]
    assert count == 1
