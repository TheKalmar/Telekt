"""Deterministic authorization policy for proposed agent actions."""

from digital_company.models import ActionType, PolicyDecision, PolicyDocument, TaskProposal


class Governor:
    """Deterministic authorization boundary; the model cannot override it."""

    def __init__(self, document: PolicyDocument | dict | None = None):
        self.document = PolicyDocument.model_validate(document or {})

    def evaluate(self, proposal: TaskProposal, remaining_budget_eur: float) -> PolicyDecision:
        """Return allow, approval-required, or deny without consulting an LLM."""
        decision_context = {
            "approval_quorum": self.document.approval_quorum,
            "approval_ttl_hours": self.document.approval_ttl_hours,
        }
        # This invariant is deliberately not configurable. A policy editor may
        # narrow AI authority but can never grant the company legal personhood.
        if proposal.action == ActionType.SIGN_CONTRACT:
            return PolicyDecision(
                outcome="deny",
                reason="AI may never sign contracts.",
                **decision_context,
            )
        if proposal.estimated_cost_eur > remaining_budget_eur:
            return PolicyDecision(
                outcome="deny",
                reason="Estimated cost exceeds remaining budget.",
                **decision_context,
            )
        if proposal.action in self.document.deny_actions:
            return PolicyDecision(
                outcome="deny",
                reason="The active company policy denies this action.",
                **decision_context,
            )
        if (
            proposal.action == ActionType.SPEND_MONEY
            and proposal.estimated_cost_eur <= self.document.autonomous_spend_limit_eur
        ):
            return PolicyDecision(
                outcome="allow",
                reason="Spend is within the active autonomous spend limit.",
                **decision_context,
            )
        if proposal.action in self.document.approval_actions:
            return PolicyDecision(
                outcome="require_approval",
                reason="External, financial, or production action requires human approval.",
                **decision_context,
            )
        return PolicyDecision(
            outcome="allow",
            reason="Internal reversible action is allowed.",
            **decision_context,
        )
