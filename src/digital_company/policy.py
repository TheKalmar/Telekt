"""Deterministic authorization policy for proposed agent actions."""

from digital_company.models import ActionType, PolicyDecision, TaskProposal


class Governor:
    """Deterministic authorization boundary; the model cannot override it."""

    def evaluate(self, proposal: TaskProposal, remaining_budget_eur: float) -> PolicyDecision:
        """Return allow, approval-required, or deny without consulting an LLM."""
        if proposal.action == ActionType.SIGN_CONTRACT:
            return PolicyDecision(outcome="deny", reason="AI may never sign contracts.")
        if proposal.estimated_cost_eur > remaining_budget_eur:
            return PolicyDecision(outcome="deny", reason="Estimated cost exceeds remaining budget.")
        if proposal.action in {
            ActionType.EXTERNAL_OUTREACH,
            ActionType.SPEND_MONEY,
            ActionType.DEPLOY_PRODUCTION,
            ActionType.REQUEST_PLATFORM_ACCESS,
            ActionType.PUBLISH_CATALOG,
            ActionType.BROWSER_OPERATE,
            ActionType.NEGOTIATE_VENDOR,
            ActionType.HIRE_VENDOR,
        }:
            return PolicyDecision(
                outcome="require_approval",
                reason="External, financial, or production action requires human approval.",
            )
        return PolicyDecision(outcome="allow", reason="Internal reversible action is allowed.")
