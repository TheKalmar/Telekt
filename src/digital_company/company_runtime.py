"""Application services shared by Temporal and lightweight execution runtimes."""

from __future__ import annotations

from typing import Protocol


class BriefMailer(Protocol):
    """Transport contract required by the stakeholder brief application flow."""

    def send_daily_brief(
        self, company_id: str, summary: dict, approvals: list[dict], settings: dict
    ) -> int: ...


def apply_orchestration_result(store, result: dict) -> None:
    """Project an orchestrator exit condition into canonical runtime control."""
    status = result.get("status", "unknown")
    if status == "waiting_for_approval":
        store.set_control("waiting_approval", "Human decision required")
    elif status == "waiting_for_human":
        store.set_control("waiting_human", result.get("reason", "Human action required"))
    elif status in {"stopped", "paused"}:
        store.set_control(status, result.get("reason"))
    elif status == "failed":
        store.set_control("error", "Approved task failed policy revalidation")


class StakeholderBriefService:
    """Prepare, rate-limit, send, and audit one consolidated company brief."""

    def __init__(self, mailer: BriefMailer, contact_interval_hours: int = 24):
        self.mailer = mailer
        self.contact_interval_hours = max(1, contact_interval_hours)

    def send_if_due(self, company_id: str, store) -> dict:
        settings = store.get_email_settings()
        if not settings["enabled"]:
            return {"status": "not_needed"}

        approvals = store.pending_approval_details()
        fresh_content_approvals = [
            item for item in approvals
            if getattr(item.get("proposal"), "action", None) is not None
            and item["proposal"].action.value == "publish_content"
            and not item.get("notified_at")
        ]
        if fresh_content_approvals:
            approvals = fresh_content_approvals
        snapshot = None if approvals else store.snapshot()
        if not approvals and not snapshot.completed_tasks:
            return {"status": "not_needed"}
        if not fresh_content_approvals and not store.stakeholder_notification_allowed(self.contact_interval_hours):
            return {"status": "rate_limited"}

        try:
            snapshot = snapshot or store.snapshot()
            sent_count = self.mailer.send_daily_brief(
                company_id,
                {
                    "control": store.get_control()["state"],
                    "spent": snapshot.spent_eur,
                    "remaining": snapshot.remaining_budget_eur,
                    "results": [task["title"] for task in snapshot.completed_tasks[-8:]],
                },
                approvals,
                settings,
            )
            if not isinstance(sent_count, int) or isinstance(sent_count, bool):
                raise TypeError("Daily brief mailer must return an integer recipient count")
            if sent_count:
                if hasattr(store, "mark_approvals_notified"):
                    store.mark_approvals_notified([item["id"] for item in approvals])
                store.audit("stakeholder.notification_sent", {
                    "channel": "content_review" if fresh_content_approvals else "daily_ceo_brief",
                    "recipients": sent_count,
                    "approval_ids": [item["id"] for item in approvals],
                })
            return {
                "status": "sent" if sent_count else "not_configured",
                "recipients": sent_count,
            }
        except Exception as exc:
            detail = f"{type(exc).__name__}: {exc}"
            store.audit("stakeholder.notification_failed", {
                "channel": "daily_ceo_brief", "error": detail,
            })
            return {"status": "failed", "error": detail}
