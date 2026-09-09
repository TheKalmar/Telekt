import json
from pathlib import Path

from digital_company.models import ActionType, TaskProposal
from digital_company.store import CompanyStore


def test_company_budget_snapshot(tmp_path: Path):
    store = CompanyStore(tmp_path / "company.db")
    store.initialize("Build a B2B MVP", 1000)
    snapshot = store.snapshot()
    assert snapshot.goal == "Build a B2B MVP"
    assert snapshot.remaining_budget_eur == 1000
    assert snapshot.pending_approvals == []


def test_stakeholder_directive_is_durable(tmp_path: Path):
    store = CompanyStore(tmp_path / "company.db")
    store.initialize("Build a B2B MVP", 1000)
    message_id = store.add_stakeholder_message("Focus on agencies", "directive")
    snapshot = store.snapshot()
    assert snapshot.stakeholder_messages[0]["id"] == message_id
    assert snapshot.stakeholder_messages[0]["status"] == "pending"
    store.address_messages([message_id], "I will test that segment first.")
    assert store.snapshot().stakeholder_messages[0]["status"] == "addressed"


def test_runtime_control_state(tmp_path: Path):
    store = CompanyStore(tmp_path / "company.db")
    store.initialize("Build a B2B MVP", 1000)
    store.set_control("running", "Working")
    assert store.get_control()["state"] == "running"


def test_model_mode_defaults_local_and_can_switch(tmp_path: Path):
    store = CompanyStore(tmp_path / "company.db")
    store.initialize("Build a B2B MVP", 1000)
    assert store.get_settings()["model_mode"] == "local"
    store.set_model_mode("hybrid")
    assert store.get_settings()["model_mode"] == "hybrid"


def test_local_model_can_be_changed_per_company(tmp_path: Path):
    store = CompanyStore(tmp_path / "company.db")
    store.initialize("Build a product", 1000)
    store.set_model_settings("local", "llama3.1:8b")
    assert store.get_settings()["local_model"] == "llama3.1:8b"


def test_cloud_fallback_is_explicit_and_disabled_by_default(tmp_path: Path):
    store = CompanyStore(tmp_path / "company.db")
    store.initialize("Build a product", 1000)
    assert store.get_settings()["allow_cloud_fallback"] == 0
    store.set_model_settings("local", "llama3.1:8b", allow_cloud_fallback=True)
    assert store.get_settings()["allow_cloud_fallback"] == 1


def test_cloud_provider_and_model_are_selected_per_company(tmp_path: Path):
    store = CompanyStore(tmp_path / "company.db")
    store.initialize("Build a product", 1000)
    store.set_model_settings("cloud", "deepseek-company:8b", False, "anthropic", "claude-opus-5")
    settings = store.get_settings()
    assert settings["cloud_provider"] == "anthropic"
    assert settings["cloud_model"] == "claude-opus-5"


def test_cloud_transport_is_not_limited_to_hard_coded_vendors(tmp_path: Path):
    store = CompanyStore(tmp_path / "company.db")
    store.initialize("Use a configured model gateway", 1000)
    store.set_model_settings(
        "cloud",
        "local-model",
        cloud_provider="litellm",
        cloud_model="gateway/custom-model",
    )
    assert store.get_settings()["cloud_provider"] == "litellm"


def test_operations_projects_live_browser_mission(tmp_path: Path):
    store = CompanyStore(tmp_path / "company.db")
    store.initialize("Research pricing", 1000)
    store.audit(
        "browser.mission_started",
        {
            "task_id": "task-1",
            "objective": "Inspect competitor pricing",
            "allowed_domains": ["example.com"],
            "max_steps": 12,
        },
    )
    store.audit(
        "browser.mission_action",
        {
            "task_id": "task-1",
            "step": 2,
            "kind": "click",
            "url": "https://example.com/pricing",
        },
    )
    mission = store.operations_data()["browser_mission"]
    assert mission["status"] == "running"
    assert mission["step"] == 2
    assert mission["last_action"]["kind"] == "click"


def test_snapshot_compacts_completed_history_for_model_context(tmp_path: Path):
    store = CompanyStore(tmp_path / "company.db")
    store.initialize("Run a compact organization", 1000)
    for index in range(30):
        proposal = TaskProposal(
            action=ActionType.RESEARCH_CONTENT,
            title=f"Topic {index}",
            objective="Verify it",
            rationale="Maintain useful context",
            expected_evidence=["Sources"],
            specialist="research",
        )
        task_id = store.create_task(proposal, "proposed")
        store.db.execute(
            "UPDATE tasks SET status='completed',result_json=?,completed_at=? WHERE id=?",
            (
                json.dumps(
                    {
                        "status": "completed",
                        "summary": "x" * 5000,
                        "evidence": ["e" * 1000] * 10,
                        "sources": [f"https://example.com/{value}" for value in range(12)],
                        "content_package": {
                            "title": f"Topic {index}",
                            "slug": f"topic-{index}",
                            "html_content": "h" * 100_000,
                        },
                        "recommendation": "r" * 3000,
                    }
                ),
                f"2026-01-{(index % 28) + 1:02d}T00:00:00+00:00",
                task_id,
            ),
        )
    store.db.commit()

    snapshot = store.snapshot()

    assert len(snapshot.completed_tasks) == 24
    result = snapshot.completed_tasks[-1]["result"]
    assert len(result["summary"]) == 2000
    assert len(result["evidence"]) == 4
    assert len(result["sources"]) == 8
    assert "html_content" not in result["content_package"]
