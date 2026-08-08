from pathlib import Path

from digital_company.models import ActionType, TaskProposal
from digital_company.store import CompanyStore


def proposal():
    return TaskProposal(action=ActionType.EXTERNAL_OUTREACH, title="Validate demand",
                        objective="Contact the shortlisted buyers", rationale="Need demand evidence",
                        expected_evidence=["Replies"], specialist="growth")


def test_duplicate_pending_approval_is_detected(tmp_path: Path):
    store = CompanyStore(tmp_path / "company.db")
    store.initialize("Goal", 100)
    item = proposal()
    store.request_approval(store.create_task(item, "proposed"), item, "External")
    assert store.has_pending_equivalent_approval(item)


def test_stakeholder_notification_is_limited_to_one_window(tmp_path: Path):
    store = CompanyStore(tmp_path / "company.db")
    store.initialize("Goal", 100)
    assert store.stakeholder_notification_allowed(24)
    store.audit("stakeholder.notification_sent", {"channel": "email"})
    assert not store.stakeholder_notification_allowed(24)
