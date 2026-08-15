from pathlib import Path

from digital_company.registry import CompanyRegistry


def company_payload(name: str) -> dict:
    return {
        "name": name, "company_type": "eCommerce", "concept": "Niche store",
        "description": "", "goal": "Validate demand", "budget": 750,
        "currency": "EUR", "target_market": "EU consumers", "customer_type": "B2C",
        "time_horizon_days": 45, "risk_tolerance": "medium", "autonomy_level": "balanced",
        "constraints": ["Approval before spending"], "success_criteria": ["First pilot"],
    }


def test_companies_have_isolated_state_and_can_be_selected(tmp_path: Path):
    registry = CompanyRegistry(tmp_path / ".company")
    first = registry.create(company_payload("Alpha Store"))
    second = registry.create(company_payload("Beta Store"))
    assert registry.active_id() == second["id"]
    registry.store_for(first["id"]).add_stakeholder_message("Alpha only")
    assert len(registry.store_for(first["id"]).snapshot().stakeholder_messages) == 1
    assert registry.store_for(second["id"]).snapshot().stakeholder_messages == []
    registry.select(first["id"])
    assert registry.active_id() == first["id"]


def test_new_company_does_not_implicitly_hire_a_ceo(tmp_path: Path):
    registry = CompanyRegistry(tmp_path / ".company")
    company = registry.create(company_payload("Agent-free company"))

    with registry.store_for(company["id"]) as store:
        assert store.list_agents() == []


def test_empty_registry_can_be_listed(tmp_path: Path):
    registry = CompanyRegistry(tmp_path / ".company")
    assert registry.list() == []


def test_work_lease_prevents_duplicate_workers(tmp_path: Path):
    registry = CompanyRegistry(tmp_path / ".company")
    company = registry.create(company_payload("Lease Test"))
    assert registry.claim_work(company["id"], "worker-a") is True
    assert registry.claim_work(company["id"], "worker-b") is False
    registry.release_work(company["id"], "worker-a")
    assert registry.claim_work(company["id"], "worker-b") is True


def test_worker_heartbeat_reports_liveness(tmp_path: Path):
    registry = CompanyRegistry(tmp_path / ".company")
    assert registry.worker_status()["status"] == "offline"
    registry.heartbeat_worker("test-worker")
    assert registry.worker_status()["status"] == "online"
