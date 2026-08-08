from digital_company.models import ActionType, TaskProposal
from digital_company.policy import Governor


def proposal(action: ActionType, cost: float = 0) -> TaskProposal:
    return TaskProposal(action=action, title="Test task", objective="Test objective",
                        rationale="Test rationale", expected_evidence=["result"],
                        estimated_cost_eur=cost, specialist="research")


def test_internal_work_is_allowed():
    assert Governor().evaluate(proposal(ActionType.RESEARCH_MARKET), 1000).outcome == "allow"


def test_external_work_requires_approval():
    assert Governor().evaluate(proposal(ActionType.EXTERNAL_OUTREACH), 1000).outcome == "require_approval"


def test_platform_evaluation_is_allowed_but_access_and_publication_need_approval():
    governor = Governor()
    assert governor.evaluate(proposal(ActionType.EVALUATE_PLATFORM), 1000).outcome == "allow"
    access = TaskProposal(
        action=ActionType.REQUEST_PLATFORM_ACCESS, title="Request Shopify access",
        objective="Connect the selected platform", rationale="Avoid custom commerce development",
        expected_evidence=["Configured access"], specialist="platform",
        platform_candidate="shopify", required_capabilities=["write_products"],
    )
    assert governor.evaluate(access, 1000).outcome == "require_approval"
    assert governor.evaluate(proposal(ActionType.PUBLISH_CATALOG), 1000).outcome == "require_approval"


def test_contract_is_denied():
    assert Governor().evaluate(proposal(ActionType.SIGN_CONTRACT), 1000).outcome == "deny"


def test_over_budget_is_denied():
    assert Governor().evaluate(proposal(ActionType.RESEARCH_MARKET, 1001), 1000).outcome == "deny"
