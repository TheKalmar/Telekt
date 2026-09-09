from types import SimpleNamespace

from digital_company.company_runtime import StakeholderBriefService, apply_orchestration_result
from digital_company.models import ActionType, TaskProposal


class FakeStore:
    def __init__(self, *, enabled=True, approvals=None, completed=None, due=True):
        self.enabled = enabled
        self.approvals = approvals or []
        self.snapshot_value = SimpleNamespace(
            completed_tasks=completed or [],
            spent_eur=12,
            remaining_budget_eur=88,
        )
        self.due = due
        self.control = None
        self.events = []

    def get_email_settings(self):
        return {"enabled": self.enabled}

    def pending_approval_details(self):
        return self.approvals

    def snapshot(self):
        return self.snapshot_value

    def stakeholder_notification_allowed(self, hours):
        return self.due and hours == 24

    def get_control(self):
        return {"state": "waiting_approval"}

    def audit(self, event, payload):
        self.events.append((event, payload))

    def set_control(self, state, detail=None):
        self.control = (state, detail)


class FakeMailer:
    def __init__(self, recipients=0):
        self.recipients = recipients
        self.calls = []

    def send_daily_brief(self, company_id, summary, approvals, settings):
        self.calls.append((company_id, summary, approvals, settings))
        return self.recipients

    def send_content_review(self, company_id, summary, approval, settings):
        self.calls.append((company_id, summary, [approval], settings))
        return self.recipients


def test_brief_service_batches_and_audits_one_delivery():
    store = FakeStore(approvals=[{"id": "approval-1"}])
    mailer = FakeMailer(1)

    result = StakeholderBriefService(mailer).send_if_due("company-1", store)

    assert result == {"status": "sent", "recipients": 1}
    assert mailer.calls[0][1]["remaining"] == 88
    assert store.events == [
        (
            "stakeholder.notification_sent",
            {"channel": "daily_ceo_brief", "recipients": 1, "approval_ids": ["approval-1"]},
        )
    ]


def test_brief_service_skips_disabled_and_rate_limited_delivery():
    mailer = FakeMailer(1)
    assert (
        StakeholderBriefService(mailer).send_if_due("company-1", FakeStore(enabled=False))["status"]
        == "not_needed"
    )
    assert (
        StakeholderBriefService(mailer).send_if_due(
            "company-1", FakeStore(approvals=[{"id": "a"}], due=False)
        )["status"]
        == "rate_limited"
    )
    assert mailer.calls == []


def test_brief_service_rejects_a_mailer_contract_mismatch():
    store = FakeStore(approvals=[{"id": "approval-1"}])
    mailer = FakeMailer(["owner@example.com"])

    result = StakeholderBriefService(mailer).send_if_due("company-1", store)

    assert result["status"] == "failed"
    assert "integer recipient count" in result["error"]
    assert [event for event, _ in store.events] == ["stakeholder.notification_failed"]


def test_orchestration_result_has_one_shared_runtime_projection():
    store = FakeStore()
    apply_orchestration_result(store, {"status": "waiting_for_human", "reason": "Login"})
    assert store.control == ("waiting_human", "Login")


def test_content_reviews_are_separate_parallel_email_threads():
    def review(approval_id, work_item_id):
        return {
            "id": approval_id,
            "notified_at": None,
            "followup_due": False,
            "proposal": TaskProposal(
                action=ActionType.PUBLISH_CONTENT,
                title=f"Review {work_item_id}",
                objective="Publish one reviewed article",
                rationale="Draft is ready",
                expected_evidence=["Published URL"],
                estimated_cost_eur=0,
                specialist="growth",
                execution_mode="browser",
                handoff_url="https://example.com/wp-admin",
                work_item_id=work_item_id,
            ),
        }

    store = FakeStore(approvals=[review("a1", "topic-1"), review("a2", "topic-2")])
    mailer = FakeMailer(1)

    result = StakeholderBriefService(mailer).send_if_due("company-1", store)

    assert result == {"status": "sent", "recipients": 2, "threads": 2}
    assert [call[2][0]["id"] for call in mailer.calls] == ["a1", "a2"]
    assert store.events[-1][1]["channel"] == "content_review_threads"
