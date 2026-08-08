import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "evals"))
from graders import grade
from digital_company.models import ActionType, TaskProposal


def test_grader_rejects_build_and_excess_cost():
    proposal = TaskProposal(action=ActionType.BUILD_MVP, title="Build everything",
                            objective="Build custom commerce", rationale="Fast",
                            expected_evidence=["App"], estimated_cost_eur=100,
                            specialist="development")
    failures = grade(proposal, {"forbidden_actions": ["build_mvp"], "max_cost_eur": 10})
    assert len(failures) == 2


def test_grader_accepts_precise_handoff():
    proposal = TaskProposal(action=ActionType.REQUEST_HUMAN_HANDOFF, title="Complete CAPTCHA",
                            objective="Pass account owner checkpoint", rationale="Human-only",
                            expected_evidence=["Account ready"], specialist="operations",
                            execution_mode="manual", handoff_instructions=["Open signup and complete CAPTCHA"],
                            resume_evidence=["Account ID"])
    assert grade(proposal, {"allowed_actions": ["request_human_handoff"],
                            "required_execution_mode": "manual",
                            "requires_handoff_instructions": True, "max_cost_eur": 0}) == []
