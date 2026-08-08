from pathlib import Path
import time

import pytest
from fastapi.testclient import TestClient

from digital_company.email_service import ApprovalMailer, approval_token, verify_approval_token
from digital_company.models import ActionType, TaskProposal
from digital_company.store import CompanyStore
from digital_company.registry import CompanyRegistry
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
