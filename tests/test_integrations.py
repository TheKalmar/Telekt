from pathlib import Path

from digital_company.models import ActionType, TaskProposal
from digital_company.store import CompanyStore
from digital_company.integration_connectors import secret_name
from digital_company.runtime_secrets import save_secret


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


def test_provider_neutral_connection_keeps_credentials_write_only(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("COMPANY_DATA_DIR", str(tmp_path / "runtime-data"))
    store = CompanyStore(tmp_path / "company.db")
    store.initialize("Operate through APIs", 1000)
    connection_id = "crm-production"
    save_secret(secret_name(connection_id, "token"), "super-private-token")

    connection = store.upsert_integration_connection({
        "id": connection_id,
        "name": "Production CRM",
        "adapter": "http_bearer",
        "provider": "Chosen CRM vendor",
        "location": "cloud",
        "base_url": "https://api.crm.example/v2/",
        "capabilities": ["contacts.read", "contacts.write"],
        "config": {},
        "enabled": True,
    })

    assert connection["status"] == "ready"
    assert connection["base_url"] == "https://api.crm.example/v2"
    assert connection["secret_status"] == {"token": True}
    assert "super-private-token" not in str(connection)
    capability = next(x for x in store.snapshot().capabilities if x.get("connection_id") == connection_id)
    assert capability["config"]["capabilities"] == ["contacts.read", "contacts.write"]


def test_connector_operation_is_prepared_once_with_stable_provider_key(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("COMPANY_DATA_DIR", str(tmp_path / "runtime-data"))
    store = CompanyStore(tmp_path / "company.db")
    store.initialize("Operate through APIs", 1000)
    save_secret(secret_name("catalog-api", "api_key"), "private-key")
    store.upsert_integration_connection({
        "id": "catalog-api", "name": "Catalog endpoint", "adapter": "http_api_key",
        "provider": "Commerce backend", "location": "cloud",
        "base_url": "https://catalog.example/api", "capabilities": ["products.draft"],
        "config": {"header_name": "X-API-Key"}, "enabled": True,
    })

    first = store.prepare_integration_operation(
        "company:execution:12:catalog", "catalog-api", "products.draft",
        "POST", "/products", {"title": "Draft product"},
    )
    second = store.prepare_integration_operation(
        "company:execution:12:catalog", "catalog-api", "products.draft",
        "POST", "/products", {"title": "Draft product"},
    )

    assert first["provider_idempotency_key"].startswith("telekt-")
    assert second["provider_idempotency_key"] == first["provider_idempotency_key"]
    assert second["cached"] is True


def test_connector_operation_rejects_capability_escalation_and_key_reuse(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("COMPANY_DATA_DIR", str(tmp_path / "runtime-data"))
    store = CompanyStore(tmp_path / "company.db")
    store.initialize("Operate through APIs", 1000)
    save_secret(secret_name("readonly-api", "token"), "private-token")
    store.upsert_integration_connection({
        "id": "readonly-api", "name": "Read-only CRM", "adapter": "http_bearer",
        "provider": "CRM", "location": "cloud", "base_url": "https://crm.example/api",
        "capabilities": ["contacts.read"], "config": {}, "enabled": True,
    })

    try:
        store.prepare_integration_operation(
            "company:execution:13", "readonly-api", "contacts.write",
            "POST", "/contacts", {},
        )
    except ValueError as exc:
        assert "capability" in str(exc)
    else:
        raise AssertionError("An undeclared capability must be rejected")

    store.prepare_integration_operation(
        "company:execution:13", "readonly-api", "contacts.read", "GET", "/contacts", {},
    )
    try:
        store.prepare_integration_operation(
            "company:execution:13", "readonly-api", "contacts.read", "GET", "/companies", {},
        )
    except RuntimeError as exc:
        assert "different integration operation" in str(exc)
    else:
        raise AssertionError("Idempotency-key reuse with changed input must fail")
