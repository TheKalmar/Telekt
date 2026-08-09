from types import SimpleNamespace

from digital_company.company_runtime import StakeholderBriefService, apply_orchestration_result


class FakeStore:
    def __init__(self, *, enabled=True, approvals=None, completed=None, due=True):
        self.enabled = enabled
        self.approvals = approvals or []
        self.snapshot_value = SimpleNamespace(
            completed_tasks=completed or [], spent_eur=12, remaining_budget_eur=88,
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
    def __init__(self, recipients=None):
        self.recipients = recipients or []
        self.calls = []

    def send_daily_brief(self, company_id, summary, approvals, settings):
        self.calls.append((company_id, summary, approvals, settings))
        return self.recipients


def test_brief_service_batches_and_audits_one_delivery():
    store = FakeStore(approvals=[{"id": "approval-1"}])
    mailer = FakeMailer(["owner@example.com"])

    result = StakeholderBriefService(mailer).send_if_due("company-1", store)

    assert result == {"status": "sent", "recipients": 1}
    assert mailer.calls[0][1]["remaining"] == 88
    assert store.events[0][0] == "stakeholder.notification_sent"


def test_brief_service_skips_disabled_and_rate_limited_delivery():
    mailer = FakeMailer(["owner@example.com"])
    assert StakeholderBriefService(mailer).send_if_due(
        "company-1", FakeStore(enabled=False)
    )["status"] == "not_needed"
    assert StakeholderBriefService(mailer).send_if_due(
        "company-1", FakeStore(approvals=[{"id": "a"}], due=False)
    )["status"] == "rate_limited"
    assert mailer.calls == []


def test_orchestration_result_has_one_shared_runtime_projection():
    store = FakeStore()
    apply_orchestration_result(store, {"status": "waiting_for_human", "reason": "Login"})
    assert store.control == ("waiting_human", "Login")
