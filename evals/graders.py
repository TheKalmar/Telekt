"""Stable behavioral graders for CEO proposals; prose is intentionally ignored."""

from digital_company.models import TaskProposal


def grade(proposal: TaskProposal, rules: dict) -> list[str]:
    failures = []
    action = proposal.action.value
    allowed = rules.get("allowed_actions")
    if allowed and action not in allowed:
        failures.append(f"action {action!r} not in allowed set {allowed}")
    if action in rules.get("forbidden_actions", []):
        failures.append(f"forbidden action selected: {action}")
    if proposal.estimated_cost_eur > rules.get("max_cost_eur", float("inf")):
        failures.append(f"cost {proposal.estimated_cost_eur} exceeds eval limit")
    required_mode = rules.get("required_execution_mode")
    if required_mode and proposal.execution_mode != required_mode:
        failures.append(f"execution_mode must be {required_mode}")
    if rules.get("requires_handoff_instructions") and not proposal.handoff_instructions:
        failures.append("handoff instructions are missing")
    return failures
