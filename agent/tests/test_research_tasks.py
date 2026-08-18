from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api import research_task_routes
from src.api.research_task_routes import register_research_task_routes
from src.research_tasks.service import FINAL_FIELDS, ResearchTaskService
from src.research_tasks.store import AGENT_ROLES, ResearchTaskStore


class FakeRuntime:
    def __init__(self, *, review: bool = True, fail_role: str | None = None) -> None:
        self.review = review
        self.fail_role = fail_role
        self.calls: list[dict[str, str]] = []

    def invoke(self, *, role: str, phase: str, provider: str, model: str,
               instruction: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.calls.append({"role": role, "phase": phase, "provider": provider, "model": model})
        if role == self.fail_role and phase == "RESEARCH":
            raise RuntimeError("simulated specialist failure")
        if phase == "PLANNING":
            return {"selected_agents": payload["allowed_roles"], "routing_reason": "scope routing"}
        if phase == "REVIEW_PLANNING":
            completed = list(payload["specialist_outputs"])
            return {"reviews": ([{"agent_role": completed[0], "question": "核对关键分歧"}] if self.review else [])}
        if phase in {"RESEARCH", "CROSS_REVIEW"}:
            return {
                "claims": [{"type": "FACT", "statement": f"{role} evidence", "evidence_ids": ["tdx:test:1"]}],
                "summary": f"{role} summary", "confidence": 0.7, "unknowns": [],
            }
        if phase == "FINAL":
            return {
                "task_id": "overwritten", "research_subject": payload["task"]["scope_id"],
                "executive_summary": "综合结论", "consensus": ["共识"], "disagreements": [],
                "key_findings": ["发现"], "key_risks": ["风险"],
                "evidence_summary": ["tdx:test:1"], "confidence": 0.72,
                "unresolved_questions": [], "suggested_next_action": ["补充研究"],
                "thesis_change_suggestion": "建议复核，不自动更新",
                "dossier_update_suggestion": "建议写入，不自动更新",
            }
        raise AssertionError(phase)


class ConnectionRuntime(FakeRuntime):
    def __init__(self) -> None:
        super().__init__(review=False)
        self.connection_calls: list[dict[str, str]] = []

    def invoke_with_connection(self, *, role: str, phase: str, model: str,
                               base_url: str, api_key: str, instruction: str,
                               payload: dict[str, Any]) -> dict[str, Any]:
        self.connection_calls.append({
            "role": role, "model": model, "base_url": base_url, "api_key": api_key,
        })
        return self.invoke(
            role=role, phase=phase, provider="openai", model=model,
            instruction=instruction, payload=payload,
        )


@pytest.fixture()
def store(tmp_path: Path) -> ResearchTaskStore:
    value = ResearchTaskStore(tmp_path / "research.db")
    for role in AGENT_ROLES:
        value.update_config(role, "ollama", "qwen2.5:32b", True)
    yield value
    value.close()


def service(store: ResearchTaskStore, runtime: FakeRuntime, *, scope: dict[str, Any] | None = None) -> ResearchTaskService:
    return ResearchTaskService(store, runtime, context_loader=lambda task: scope or {
        "tdx_scope": {}, "evidence": [{"id": "tdx:test:1", "source": "TDX", "published_at": "2026-08-17", "excerpt": "test", "related_object": task["scope_id"]}],
    })


def create(svc: ResearchTaskService, scope_type: str = "INDUSTRY", question: str = "行业景气如何") -> dict[str, Any]:
    return svc.create_task(source="BOSS", scope_type=scope_type, scope_id="881001.SH" if scope_type == "INDUSTRY" else "601899.SH",
                           title="研究任务", question=question, requested_by="boss", trigger_context={})


def test_agent_role_configs_persist_provider_and_model(store: ResearchTaskStore) -> None:
    assert [row["role"] for row in store.list_configs()] == list(AGENT_ROLES)
    store.update_config("industry", "ollama", "model-b", False)
    assert store.get_config("industry") | {"updated_at": "x"} == {
        "role": "industry", "provider": "ollama", "model": "model-b",
        "base_url": "", "api_key_configured": False,
        "enabled": False, "updated_at": "x"
    }


def test_industry_task_state_machine_and_single_review(store: ResearchTaskStore) -> None:
    runtime = FakeRuntime(review=True)
    svc = service(store, runtime)
    task = create(svc, question="利率和政策如何影响行业")
    result = svc.run_task(task["id"])
    assert result["status"] == "COMPLETED"
    assert [item["status"] for item in result["status_history"]] == ["CREATED", "RESEARCHING", "REVIEWING", "COMPLETED"]
    assert result["selected_agents"] == ["macro_policy", "industry", "risk"]
    assert result["review_count"] == 1
    assert sum(call["phase"] == "CROSS_REVIEW" for call in runtime.calls) <= 4
    assert set(FINAL_FIELDS) <= set(result["result_summary"])
    assert result["result_summary"]["task_id"] == task["id"]


def test_company_task_adds_valuation_only_when_data_exists(store: ResearchTaskStore) -> None:
    runtime = FakeRuntime(review=False)
    svc = service(store, runtime, scope={"tdx_scope": {"pe": 12.5}, "evidence": [{"id": "tdx:test:1"}]})
    result = svc.run_task(create(svc, "COMPANY", "公司质量与估值如何")["id"])
    assert result["selected_agents"] == ["company", "industry", "valuation", "risk"]
    assert result["review_count"] == 0


def test_disabled_agent_is_not_selected(store: ResearchTaskStore) -> None:
    store.update_config("risk", "ollama", "qwen2.5:32b", False)
    runtime = FakeRuntime(review=False)
    svc = service(store, runtime)
    result = svc.run_task(create(svc)["id"])
    assert "risk" not in result["selected_agents"]
    assert not any(call["role"] == "risk" for call in runtime.calls)


def test_participants_record_each_roles_actual_model(store: ResearchTaskStore) -> None:
    store.update_config("industry", "ollama", "model-b", True)
    store.update_config("risk", "ollama", "model-c", True)
    runtime = FakeRuntime(review=False)
    svc = service(store, runtime)
    task = svc.run_task(create(svc)["id"])
    actual = {(row["agent_role"], row["model"]) for row in svc.get_participants(task["id"]) if row["phase"] == "RESEARCH"}
    assert ("industry", "model-b") in actual
    assert ("risk", "model-c") in actual


def test_research_runtime_uses_each_roles_direct_connection(store: ResearchTaskStore) -> None:
    store.update_connection(
        "industry", base_url="https://research.example.com/v1", model="industry-model",
        api_key="industry-key", clear_api_key=False, enabled=True,
    )
    runtime = ConnectionRuntime()
    service(store, runtime).run_task(create(service(store, runtime))["id"])
    industry = next(call for call in runtime.connection_calls if call["role"] == "industry")
    assert industry == {
        "role": "industry", "model": "industry-model",
        "base_url": "https://research.example.com/v1", "api_key": "industry-key",
    }


def test_specialist_failure_is_auditable_and_other_agents_finish(store: ResearchTaskStore) -> None:
    runtime = FakeRuntime(review=False, fail_role="risk")
    svc = service(store, runtime)
    task = svc.run_task(create(svc)["id"])
    participants = svc.get_participants(task["id"])
    failed = [row for row in participants if row["agent_role"] == "risk" and row["phase"] == "RESEARCH"]
    assert task["status"] == "COMPLETED"
    assert failed[0]["status"] == "FAILED"
    assert "simulated specialist failure" in failed[0]["error"]


def test_unconfigured_lead_blocks_before_invocation(store: ResearchTaskStore, monkeypatch: pytest.MonkeyPatch) -> None:
    store.update_config("research_lead", "openai", "gpt-5.5", True)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    runtime = FakeRuntime()
    svc = service(store, runtime)
    result = svc.run_task(create(svc)["id"])
    assert result["status"] == "BLOCKED"
    assert result["error"] == "BLOCKED_BY_CONFIGURATION"
    assert runtime.calls == []


def test_agent_api_never_exposes_key_fields(store: ResearchTaskStore) -> None:
    store.update_connection(
        "industry", base_url="https://research.example.com/v1", model="model-b",
        api_key="test-secret-value", clear_api_key=False, enabled=True,
    )
    svc = service(store, FakeRuntime())
    research_task_routes._service = svc
    app = FastAPI()
    register_research_task_routes(app, require_auth=lambda: True)
    client = TestClient(app)
    response = client.get("/api/agents/config")
    providers = client.get("/api/agents/providers")
    model_settings = client.get("/api/agents/model-settings")
    connections = client.get("/api/agents/connections")
    assert response.status_code == providers.status_code == model_settings.status_code == connections.status_code == 200
    serialized = response.text.lower() + providers.text.lower() + model_settings.text.lower() + connections.text.lower()
    assert "test-secret-value" not in serialized
    assert "secret" not in serialized
    assert len(response.json()["items"]) == len(AGENT_ROLES)
    assert "provider" not in model_settings.text.lower()
    research_task_routes._service = None


def test_per_researcher_connection_persists_secret_without_returning_it(store: ResearchTaskStore) -> None:
    svc = service(store, FakeRuntime())
    updated = svc.update_connection_setting(
        "industry",
        base_url="https://research.example.com/v1/",
        model="deepseek-v4-pro",
        api_key="test-secret-value",
        clear_api_key=False,
        enabled=True,
    )

    assert updated == {
        "role": "industry",
        "base_url": "https://research.example.com/v1",
        "model": "deepseek-v4-pro",
        "api_key_configured": True,
        "enabled": True,
        "ready": True,
        "updated_at": updated["updated_at"],
    }
    assert "api_key" not in updated
    assert store.get_runtime_config("industry")["api_key"] == "test-secret-value"


def test_model_only_settings_update_internal_config(store: ResearchTaskStore) -> None:
    svc = service(store, FakeRuntime())
    settings = svc.get_model_settings()
    industry = next(item for item in settings if item["role"] == "industry")
    target = next(item for item in industry["models"] if item["name"] != industry["model_name"])

    updated = svc.update_model_setting("industry", model_id=target["id"], enabled=False)

    assert updated["model_name"] == target["name"]
    assert updated["enabled"] is False
    assert "provider" not in updated
    assert store.get_config("industry")["model"] == target["name"]


def test_track_classifier_does_not_offer_ollama_model(store: ResearchTaskStore) -> None:
    settings = service(store, FakeRuntime()).get_model_settings()
    classifier = next(item for item in settings if item["role"] == "track_classifier")
    assert classifier["ready"] is False
    assert "qwen2.5:32b" not in {item["name"] for item in classifier["models"]}
