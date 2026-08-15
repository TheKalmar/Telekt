from pathlib import Path
import time

import pytest
from fastapi.testclient import TestClient

from digital_company.email_service import ApprovalMailer, approval_token, verify_approval_token
from digital_company.models import ActionType, TaskProposal
from digital_company.store import CompanyStore
from digital_company.registry import CompanyRegistry
from digital_company.integration_connectors import secret_name
from digital_company.runtime_secrets import save_secret
from digital_company import web


def proposal() -> TaskProposal:
    return TaskProposal(
        action=ActionType.SPEND_MONEY,
        title="Buy validation ads",
        objective="Run a small demand experiment",
        rationale="Measure qualified demand",
        expected_evidence=["Qualified leads"],
        estimated_cost_eur=100,
        specialist="growth",
    )


def test_approval_links_are_recipient_specific(monkeypatch):
    monkeypatch.setenv("APPROVAL_SIGNING_SECRET", "test-secret-with-enough-entropy")
    expires = int(time.time()) + 60
    token = approval_token("company-1", "approval-1", "owner@example.com", expires)
    assert verify_approval_token("company-1", "approval-1", "owner@example.com", expires, token)
    assert not verify_approval_token("company-1", "approval-1", "other@example.com", expires, token)
    assert not verify_approval_token("company-1", "approval-1", "owner@example.com", 1, token)


def test_decline_requires_reason_and_feedback_reaches_snapshot(tmp_path: Path):
    store = CompanyStore(tmp_path / "company.db")
    store.initialize("Validate demand", 1000)
    task_id = store.create_task(proposal(), "proposed")
    approval_id = store.request_approval(task_id, proposal(), "Approval required")
    with pytest.raises(ValueError):
        store.reject(approval_id, "")
    store.reject(approval_id, "CAC assumption is too optimistic", "finance@example.com")
    message = store.snapshot().stakeholder_messages[0]
    assert message["kind"] == "approval_feedback"
    assert "CAC assumption" in message["content"]


def test_mailer_sends_individual_html_messages(monkeypatch):
    sent = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            assert host == "mailpit"
            assert port == 1025

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def send_message(self, message):
            sent.append(message)

    monkeypatch.setenv("APPROVAL_SIGNING_SECRET", "test-secret")
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://localhost:8421")
    monkeypatch.setenv("SMTP_HOST", "mailpit")
    monkeypatch.setenv("SMTP_PORT", "1025")
    monkeypatch.setenv("SMTP_FROM", "approvals@example.com")
    monkeypatch.setenv("SMTP_TLS", "false")
    monkeypatch.setattr("digital_company.email_service.smtplib.SMTP", FakeSMTP)
    count = ApprovalMailer().send(
        "company-1", "approval-1", proposal(), "Human approval required",
        {"enabled": True, "approvers": ["a@example.com", "b@example.com"], "sender_name": "Acme AI"},
    )
    assert count == 2
    assert {message["To"] for message in sent} == {"a@example.com", "b@example.com"}
    assert all("Review decision" in str(message) for message in sent)


def test_daily_brief_batches_all_pending_decisions(monkeypatch):
    sent = []
    class FakeSMTP:
        def __init__(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def send_message(self, message): sent.append(message)
    monkeypatch.setenv("APPROVAL_SIGNING_SECRET", "test-secret")
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://localhost:8421")
    monkeypatch.setenv("SMTP_HOST", "mailpit")
    monkeypatch.setenv("SMTP_FROM", "brief@example.com")
    monkeypatch.setenv("SMTP_TLS", "false")
    monkeypatch.setattr("digital_company.email_service.smtplib.SMTP", FakeSMTP)
    approvals = [{"id": "a1", "proposal": proposal()}, {"id": "a2", "proposal": proposal()}]
    count = ApprovalMailer().send_daily_brief("company-1", {
        "control": "running", "spent": 2.5, "remaining": 997.5, "results": ["Market research completed"],
    }, approvals, {"enabled": True, "approvers": ["owner@example.com"], "sender_name": "Acme AI"})
    assert count == 1
    body = str(sent[0])
    assert "Daily CEO brief" in body
    assert "Market research completed" in body
    assert body.count("Review decision") == 2


def test_content_review_email_renders_full_safe_article(monkeypatch):
    sent = []
    class FakeSMTP:
        def __init__(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def send_message(self, message): sent.append(message)
    monkeypatch.setenv("APPROVAL_SIGNING_SECRET", "test-secret")
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://localhost:8421")
    monkeypatch.setenv("SMTP_HOST", "mailpit")
    monkeypatch.setenv("SMTP_FROM", "brief@example.com")
    monkeypatch.setenv("SMTP_TLS", "false")
    monkeypatch.setattr("digital_company.email_service.smtplib.SMTP", FakeSMTP)
    content_proposal = proposal().model_copy(update={
        "action": ActionType.PUBLISH_CONTENT, "execution_mode": "browser",
        "handoff_url": "https://example.com/wp-admin/post.php?post=42",
    })
    review = {
        "content_package": {
            "html_content": "<h1>Unpaid wages</h1><script>alert(1)</script><p>Complete article.</p>",
            "focus_keyword": "unpaid wages", "categories": ["Employment law"],
            "tags": ["wages", "workers"], "source_urls": ["https://example.com/law"],
        },
        "quality_report": {"score": 88, "maximum": 100, "target": 70},
    }
    ApprovalMailer().send_daily_brief("company-1", {
        "control": "waiting_approval", "spent": 1, "remaining": 99, "results": [],
    }, [{"id": "approval-content", "proposal": content_proposal, "review": review}], {
        "enabled": True, "approvers": ["owner@example.com"], "sender_name": "G&K",
    })
    body = str(sent[0])
    assert "Complete article" in body
    assert "Telekt SEO QA" in body
    assert "alert(1)" not in body
    assert sent[0]["Subject"].startswith("Content review:")
    assert sent[0]["X-Telekt-Work-Item"] == "approval-content"
    assert sent[0]["References"].startswith("<telekt-content-")


def test_mailer_uses_selected_write_only_smtp_connection(tmp_path: Path, monkeypatch):
    sent = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            assert (host, port, timeout) == ("smtp.example.com", 587, 15)
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def starttls(self): self.started_tls = True
        def login(self, username, password):
            assert (username, password) == ("sender@example.com", "app-password")
        def send_message(self, message): sent.append(message)

    monkeypatch.setenv("COMPANY_DATA_DIR", str(tmp_path / "runtime-data"))
    monkeypatch.setenv("APPROVAL_SIGNING_SECRET", "test-secret")
    monkeypatch.setattr("digital_company.email_service.smtplib.SMTP", FakeSMTP)
    store = CompanyStore(tmp_path / "company.db")
    store.initialize("Send approval", 0)
    connection_id = "approval-smtp"
    save_secret(secret_name(connection_id, "username"), "sender@example.com")
    save_secret(secret_name(connection_id, "password"), "app-password")
    store.upsert_integration_connection({
        "id": connection_id, "name": "Approval SMTP", "adapter": "smtp",
        "provider": "Mail provider", "location": "cloud",
        "base_url": "smtp://smtp.example.com:587", "capabilities": ["email.send"],
        "config": {"security": "starttls", "authentication": "password"},
        "enabled": True,
    })
    store.set_email_settings(
        True, ["owner@example.com"], "Acme AI", connection_id,
        "sender@example.com", "https://telekt.example.com",
    )

    count = ApprovalMailer().send(
        "company-1", "approval-1", proposal(), "Owner decision", store.get_email_settings(),
    )

    assert count == 1
    assert sent[0]["From"] == "Acme AI <sender@example.com>"
    assert "https://telekt.example.com/approval/" in str(sent[0])


def test_email_review_get_is_safe_and_decline_comment_is_required(tmp_path: Path, monkeypatch):
    registry = CompanyRegistry(tmp_path / ".company")
    company = registry.create({
        "name": "Email Test", "company_type": "SaaS", "concept": "Test approvals",
        "description": "", "goal": "Test", "budget": 1000, "currency": "EUR",
        "target_market": "B2B", "customer_type": "B2B", "time_horizon_days": 30,
        "risk_tolerance": "medium", "autonomy_level": "balanced", "constraints": [],
        "success_criteria": ["Safe approval"],
    })
    store = registry.store_for(company["id"])
    task_id = store.create_task(proposal(), "proposed")
    approval_id = store.request_approval(task_id, proposal(), "Approval required")
    monkeypatch.setattr(web, "registry", registry)
    monkeypatch.setenv("APPROVAL_SIGNING_SECRET", "test-secret")
    expires = int(time.time()) + 60
    email = "owner@example.com"
    token = approval_token(company["id"], approval_id, email, expires)
    path = f"/approval/{company['id']}/{approval_id}"
    client = TestClient(web.app)

    response = client.get(path, params={"email": email, "expires": expires, "token": token})
    assert response.status_code == 200
    assert store.list_approvals()[0]["status"] == "pending"

    response = client.post(path, data={
        "email": email, "expires": expires, "token": token, "decision": "reject", "comment": "",
    })
    assert response.status_code == 400
    assert store.list_approvals()[0]["status"] == "pending"

    response = client.post(path, data={
        "email": email, "expires": expires, "token": token, "decision": "reject",
        "comment": "The budget is too high",
    })
    assert response.status_code == 200
    assert store.list_approvals()[0]["status"] == "rejected"
