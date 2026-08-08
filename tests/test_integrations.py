from pathlib import Path

from digital_company.models import ActionType, TaskProposal
from digital_company.store import CompanyStore


def test_integration_tracks_secret_presence_without_exposing_values(tmp_path: Path, monkeypatch):
    store = CompanyStore(tmp_path / "company.db")
    store.initialize("Launch a store", 1000, {"company_type": "eCommerce"})
    monkeypatch.setenv("SHOPIFY_CLIENT_ID", "private-id")

    integration = store.upsert_integration(
        "shopify", "configured",
        {"store_domain": "demo.myshopify.com", "capabilities": ["write_products"]},
        ["SHOPIFY_CLIENT_ID", "SHOPIFY_CLIENT_SECRET"],
    )

    assert integration["status"] == "missing_secrets"
    assert integration["secret_status"] == {
        "SHOPIFY_CLIENT_ID": True,
        "SHOPIFY_CLIENT_SECRET": False,
    }
    assert "private-id" not in str(integration)
    assert store.snapshot().capabilities[0]["provider"] == "shopify"


def test_platform_access_request_requires_explicit_candidate_and_capabilities():
    values = dict(
        action=ActionType.REQUEST_PLATFORM_ACCESS,
        title="Connect commerce platform",
        objective="Get minimal API access",
        rationale="Use an existing platform instead of rebuilding commerce",
        expected_evidence=["Configured integration"],
        specialist="platform",
    )

    try:
        TaskProposal(**values)
    except ValueError as exc:
        assert "platform_candidate" in str(exc)
    else:
        raise AssertionError("Incomplete access request must be rejected")

    proposal = TaskProposal(
        **values,
        platform_candidate="shopify",
        required_capabilities=["read_products", "write_products"],
    )
    assert proposal.platform_candidate == "shopify"
