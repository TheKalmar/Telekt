from pathlib import Path

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
